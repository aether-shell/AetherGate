package service

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestModerationContentDefaultsPersistWithoutUIFields(t *testing.T) {
	raw := `{"enabled":true,"engine":"openai","all_groups":false,"group_ids":[3],"auto_ban_enabled":false,"thresholds":{"sexual":0.8},"api_keys":["test-key"]}`
	cfg, err := parseContentModerationConfig(raw)
	require.NoError(t, err)
	require.Equal(t, 2000, cfg.AuditUserTextMaxChars)
	require.Equal(t, 400, cfg.AuditToolOutputMaxChars)
	require.True(t, cfg.AuditImages)
	require.True(t, cfg.AuditToolOutputs)
	repo := &contentModerationTestSettingRepo{values: map[string]string{SettingKeyContentModerationConfig: raw}}
	svc := NewContentModerationService(repo, &contentModerationTestRepo{}, nil, nil, nil, nil, nil, nil)
	enabled := false
	_, err = svc.UpdateConfig(context.Background(), UpdateContentModerationConfigInput{Enabled: &enabled})
	require.NoError(t, err)
	saved, err := parseContentModerationConfig(repo.values[SettingKeyContentModerationConfig])
	require.NoError(t, err)
	require.Equal(t, cfg.AuditUserTextMaxChars, saved.AuditUserTextMaxChars)
	require.Equal(t, cfg.AuditToolOutputMaxChars, saved.AuditToolOutputMaxChars)
	require.True(t, saved.AuditImages)
	require.True(t, saved.AuditToolOutputs)
	require.Equal(t, cfg.GroupIDs, saved.GroupIDs)
	require.Equal(t, cfg.Thresholds, saved.Thresholds)
	require.False(t, saved.AutoBanEnabled)
	require.Equal(t, cfg.APIKeys, saved.APIKeys)
	explicit, err := parseContentModerationConfig(`{"audit_images":false,"audit_tool_outputs":false,"audit_user_text_max_chars":15,"audit_tool_output_max_chars":8}`)
	require.NoError(t, err)
	require.False(t, explicit.AuditImages)
	require.False(t, explicit.AuditToolOutputs)
	require.Equal(t, 15, explicit.AuditUserTextMaxChars)
}

func TestModerationAuditInputBudgetsAreCumulativeAndDoNotMutateOriginal(t *testing.T) {
	body := []byte(`{"messages":[{"role":"assistant","content":"old answer"},{"role":"user","content":[{"type":"text","text":"甲乙"},{"type":"text","text":"丙丁戊"}]},{"role":"tool","content":[{"type":"text","text":"一二"},{"type":"text","text":"三四五"}]}]}`)
	input := ExtractContentModerationInput(ContentModerationProtocolOpenAIChat, body)
	before, err := json.Marshal(input)
	require.NoError(t, err)
	cfg := defaultContentModerationConfig()
	cfg.AuditUserTextMaxChars, cfg.AuditToolOutputMaxChars = 3, 4
	audit := contentModerationAuditInput(input, cfg)
	require.Equal(t, "甲乙\n丙\n一二\n三四", audit.Text)
	after, err := json.Marshal(input)
	require.NoError(t, err)
	require.Equal(t, before, after)
	require.Equal(t, "甲乙\n丙丁戊\n一二\n三四五", input.Text)
	cfg.AuditToolOutputs = false
	require.Equal(t, "甲乙\n丙", contentModerationAuditInput(input, cfg).Text)
}

func TestModerationCurrentTurnIncludesAllUserAndToolInputs(t *testing.T) {
	for protocol, body := range map[string]string{
		ContentModerationProtocolOpenAIChat:        `{"messages":[{"role":"user","content":"history"},{"role":"assistant","content":"answer"},{"role":"user","content":"first"},{"role":"user","content":"second"},{"role":"tool","content":"result"}]}`,
		ContentModerationProtocolAnthropicMessages: `{"messages":[{"role":"user","content":"history"},{"role":"assistant","content":"answer"},{"role":"user","content":[{"type":"text","text":"first"},{"type":"text","text":"second"},{"type":"tool_result","content":"result"}]}]}`,
		ContentModerationProtocolOpenAIResponses:   `{"input":[{"role":"user","content":"history"},{"type":"function_call","name":"lookup"},{"role":"user","content":"first"},{"role":"user","content":"second"},{"type":"function_call_output","output":"result"}]}`,
		ContentModerationProtocolGemini:            `{"contents":[{"role":"user","parts":[{"text":"history"}]},{"role":"model","parts":[{"text":"answer"}]},{"role":"user","parts":[{"text":"first"},{"text":"second"},{"functionResponse":{"response":"result"}}]}]}`,
	} {
		t.Run(protocol, func(t *testing.T) {
			input := ExtractContentModerationInput(protocol, []byte(body))
			require.Equal(t, "first\nsecond\nresult", input.Text)
			require.Equal(t, ContentModerationSourceMixed, input.Source)
			require.Len(t, input.Items, 3)
			require.Equal(t, ContentModerationSourceTool, input.Items[2].Source)
			require.Contains(t, extractContentModerationKeywordText(protocol, []byte(body)), "result")
		})
	}
}

func TestModerationStructuredToolPrefixRetainsOriginalFieldOrder(t *testing.T) {
	raw := `{"z":"` + strings.Repeat("甲", 600) + `", "a":"tail"}`
	body := []byte(`{"input":[{"type":"function_call_output","output":` + raw + `}]}`)
	input := ExtractContentModerationInput(ContentModerationProtocolOpenAIResponses, body)
	audit := contentModerationAuditInput(input, defaultContentModerationConfig())
	require.Equal(t, string([]rune(raw)[:400]), audit.Text)
	require.Equal(t, raw, input.Text)
}

func TestModerationHashIncludesSourceAndTextPastAuditLimit(t *testing.T) {
	user := ExtractContentModerationInput(ContentModerationProtocolOpenAIChat, []byte(`{"messages":[{"role":"user","content":"same"}]}`))
	tool := ExtractContentModerationInput(ContentModerationProtocolOpenAIChat, []byte(`{"messages":[{"role":"tool","content":"same"}]}`))
	require.NotEqual(t, user.Hash(), tool.Hash())
	a := ContentModerationInput{Text: strings.Repeat("x", 15000) + "a"}
	b := ContentModerationInput{Text: strings.Repeat("x", 15000) + "b"}
	require.NotEqual(t, a.Hash(), b.Hash())
}

func TestModerationImageAndToolSwitches(t *testing.T) {
	body := []byte(`{"messages":[{"role":"assistant","content":"answer"},{"role":"tool","content":[{"type":"text","text":"result"},{"type":"image_url","image_url":{"url":"https://example.com/shared.png"}}]},{"role":"user","content":[{"type":"text","text":"user"},{"type":"image_url","image_url":{"url":"https://example.com/shared.png"}},{"type":"image_url","image_url":{"url":"https://example.com/user.png"}}]}]}`)
	input := ExtractContentModerationInput(ContentModerationProtocolOpenAIChat, body)
	cfg := defaultContentModerationConfig()
	require.Len(t, contentModerationAuditInput(input, cfg).Images, 2)
	cfg.AuditToolOutputs = false
	audit := contentModerationAuditInput(input, cfg)
	require.Equal(t, "user", audit.Text)
	require.Equal(t, []string{"https://example.com/shared.png", "https://example.com/user.png"}, audit.Images)
	cfg.AuditImages = false
	require.Empty(t, contentModerationAuditInput(input, cfg).Images)
	cfg.AuditToolOutputs = true
	audit = contentModerationAuditInput(input, cfg)
	require.Equal(t, "result\nuser", audit.Text)
	require.Empty(t, audit.Images)
	structured := ExtractContentModerationInput(ContentModerationProtocolOpenAIResponses, []byte(`{"input":[{"type":"function_call_output","output":{"answer":"text","attachments":[{"type":"image","source":{"media_type":"image/png","data":"aGVsbG8="}}]}}]}`))
	require.Len(t, structured.Images, 1)
	audit = contentModerationAuditInput(structured, cfg)
	require.Contains(t, audit.Text, "text")
	require.NotContains(t, audit.Text, "aGVsbG8=")
	require.Empty(t, audit.Images)
}

func TestModerationCheckSendsPrefixesAndRetainsFullReview(t *testing.T) {
	for _, mode := range []string{ContentModerationModePreBlock, ContentModerationModeObserve} {
		t.Run(mode, func(t *testing.T) {
			userText, toolText := strings.Repeat("甲", 5000)+"user-tail", strings.Repeat("乙", 1000)+"tool-tail"
			body, err := json.Marshal(map[string]any{"messages": []any{map[string]any{"role": "assistant", "content": "history"}, map[string]any{"role": "user", "content": userText}, map[string]any{"role": "tool", "content": toolText}}})
			require.NoError(t, err)
			original := bytes.Clone(body)
			var calls atomic.Int32
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				calls.Add(1)
				var payload struct {
					Input string `json:"input"`
				}
				if err := json.NewDecoder(r.Body).Decode(&payload); err != nil {
					t.Error(err)
				}
				if payload.Input != strings.Repeat("甲", 2000)+"\n"+strings.Repeat("乙", 400) {
					t.Error("unexpected audit prefix")
				}
				_ = json.NewEncoder(w).Encode(moderationAPIResponse{Results: []moderationAPIResult{{CategoryScores: map[string]float64{"sexual": 0.1}}}})
			}))
			defer server.Close()
			cfg := defaultContentModerationConfig()
			cfg.Enabled, cfg.Mode, cfg.BaseURL, cfg.APIKeys, cfg.RecordNonHits = true, mode, server.URL, []string{"fake"}, true
			cfg.AutoBanEnabled, cfg.EmailOnHit = false, false
			raw, err := json.Marshal(cfg)
			require.NoError(t, err)
			repo := &contentModerationTestRepo{}
			svc := NewContentModerationService(&contentModerationTestSettingRepo{values: map[string]string{SettingKeyRiskControlEnabled: "true", SettingKeyContentModerationConfig: string(raw)}}, repo, nil, nil, nil, nil, nil, nil)
			decision, err := svc.Check(context.Background(), ContentModerationCheckInput{Protocol: ContentModerationProtocolOpenAIChat, Body: body})
			require.NoError(t, err)
			require.True(t, decision.Allowed)
			logs := requireContentModerationLogCount(t, repo, 1)
			require.Len(t, logs[0].InputItems, 2)
			require.Equal(t, userText, logs[0].InputItems[0].Text)
			require.Equal(t, toolText, logs[0].InputItems[1].Text)
			require.Equal(t, original, body)
			require.Equal(t, int32(1), calls.Load())
		})
	}
}

func TestModerationLocalKeywordChecksBeyondAuditPrefix(t *testing.T) {
	for _, role := range []string{"user", "tool"} {
		t.Run(role, func(t *testing.T) {
			cfg := defaultContentModerationConfig()
			cfg.Enabled, cfg.BlockedKeywords = true, []string{"blocked-tail"}
			cfg.AutoBanEnabled, cfg.EmailOnHit = false, false
			raw, err := json.Marshal(cfg)
			require.NoError(t, err)
			repo := &contentModerationTestRepo{}
			svc := NewContentModerationService(&contentModerationTestSettingRepo{values: map[string]string{SettingKeyRiskControlEnabled: "true", SettingKeyContentModerationConfig: string(raw)}}, repo, nil, nil, nil, nil, nil, nil)
			text := strings.Repeat("x", 15000) + "blocked-tail"
			body, err := json.Marshal(map[string]any{"messages": []any{map[string]any{"role": "assistant", "content": "old"}, map[string]any{"role": role, "content": text}}})
			require.NoError(t, err)
			decision, err := svc.Check(context.Background(), ContentModerationCheckInput{Protocol: ContentModerationProtocolOpenAIChat, Body: body})
			require.NoError(t, err)
			require.Equal(t, ContentModerationActionKeywordBlock, decision.Action)
			logs := requireContentModerationLogCount(t, repo, 1)
			require.Equal(t, text, logs[0].InputItems[0].Text)
		})
	}
}

func TestModerationAuditsEveryImageAndRetainsHitAfterFailure(t *testing.T) {
	var calls atomic.Int32
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		n := calls.Add(1)
		var payload struct {
			Input []moderationAPIInputPart `json:"input"`
		}
		if err := json.NewDecoder(r.Body).Decode(&payload); err != nil {
			t.Error(err)
		}
		images := 0
		for _, part := range payload.Input {
			if part.Type == "image_url" {
				images++
			}
		}
		if images != 1 {
			t.Errorf("images per input: %d", images)
		}
		if n == 2 {
			w.WriteHeader(http.StatusInternalServerError)
			return
		}
		_ = json.NewEncoder(w).Encode(moderationAPIResponse{Results: []moderationAPIResult{{CategoryScores: map[string]float64{"sexual": 0.99}}}})
	}))
	defer server.Close()
	cfg := defaultContentModerationConfig()
	cfg.Enabled, cfg.BaseURL, cfg.APIKeys, cfg.RetryCount = true, server.URL, []string{"fake"}, 0
	cfg.AutoBanEnabled, cfg.EmailOnHit = false, false
	svc := NewContentModerationService(&contentModerationTestSettingRepo{}, &contentModerationTestRepo{}, nil, nil, nil, nil, nil, nil)
	content := ContentModerationInput{Text: "check", Images: []string{"https://example.com/a.png", "https://example.com/b.png"}}
	decision := svc.checkSync(context.Background(), ContentModerationCheckInput{}, cfg, content, content.Hash(), nil, true)
	require.True(t, decision.Blocked)
	require.Equal(t, int32(2), calls.Load())
}
