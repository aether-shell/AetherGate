package service

import (
	"context"
	"errors"
	"fmt"
	"strings"
)

const (
	ContentModerationSourceUser    = "user"
	ContentModerationSourceTool    = "tool"
	ContentModerationSourceMixed   = "mixed"
	ContentModerationItemTypeText  = "text"
	ContentModerationItemTypeImage = "image"
)

type ContentModerationInputItem struct {
	Index    int    `json:"index"`
	Source   string `json:"source"`
	Type     string `json:"type"`
	Text     string `json:"text,omitempty"`
	ImageRef string `json:"image_ref,omitempty"`
}

type ContentModerationImage struct {
	SourceIndex int
	Source      string
	Reference   string
}

func normalizeContentModerationSource(source string) string {
	if strings.EqualFold(strings.TrimSpace(source), ContentModerationSourceTool) {
		return ContentModerationSourceTool
	}
	return ContentModerationSourceUser
}

func contentModerationInputSource(items []ContentModerationInputItem) string {
	user, tool := false, false
	for _, item := range items {
		if normalizeContentModerationSource(item.Source) == ContentModerationSourceTool {
			tool = true
		} else {
			user = true
		}
	}
	if user && tool {
		return ContentModerationSourceMixed
	}
	if tool {
		return ContentModerationSourceTool
	}
	return ContentModerationSourceUser
}

// Keep complete input out of list responses; only the administrator detail request loads it.
func moderationReviewItems(input ContentModerationCheckInput, fallback string) []ContentModerationInputItem {
	content := extractContentModerationInput(input.Protocol, input.Body, false)
	if content.IsEmpty() && fallback != "" {
		content = ContentModerationInput{Text: fallback}
		content.Normalize()
	}
	items := append([]ContentModerationInputItem(nil), content.Items...)
	for i := range items {
		items[i].Text = redactContentModerationSecrets(items[i].Text)
	}
	return items
}

// OpenAI accepts at most one image per input. Audit every image and merge the
// highest category scores, so a later failure cannot erase an earlier hit.
func (s *ContentModerationService) auditModerationContent(ctx context.Context, cfg *ContentModerationConfig, content ContentModerationInput, track bool) (*moderationAPIResult, error) {
	if cfg.Engine == ContentModerationEngineTypeSafe {
		return s.callModeration(ctx, cfg, content.ModerationInput(), track)
	}
	merged := &moderationAPIResult{CategoryScores: map[string]float64{}}
	var failures []error
	calls := 0
	call := func(input any) {
		calls++
		result, err := s.callModeration(ctx, cfg, input, track)
		if err != nil {
			failures = append(failures, fmt.Errorf("audit unit %d: %w", calls, err))
			return
		}
		merged.EngineMeta = result.EngineMeta
		merged.Flagged = merged.Flagged || result.Flagged
		for category, score := range result.CategoryScores {
			if score > merged.CategoryScores[category] {
				merged.CategoryScores[category] = score
			}
		}
	}
	text := []rune(content.Text)
	// The default 2000 + 400 character policy fits in one request. Keep larger
	// persisted limits bounded by the upstream's per-input text size as well.
	for len(text) > maxModerationInputRunes {
		call(string(text[:maxModerationInputRunes]))
		text = text[maxModerationInputRunes:]
		if ctx.Err() != nil {
			return merged, errors.Join(failures...)
		}
	}
	if len(content.Images) == 0 {
		call(string(text))
	} else {
		for i, image := range content.Images {
			unit := ContentModerationInput{Images: []string{image}}
			if i == 0 {
				unit.Text = string(text)
			}
			call(unit.ModerationInput())
			if ctx.Err() != nil {
				break
			}
		}
	}
	return merged, errors.Join(failures...)
}
