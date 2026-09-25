package service

import (
	"context"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/Wei-Shaw/sub2api/internal/config"
	"github.com/Wei-Shaw/sub2api/internal/pkg/openai"
	"github.com/gin-gonic/gin"
	"github.com/stretchr/testify/require"
)

func TestAetherGateAPIKeyAllowedClientReachesUpstream(t *testing.T) {
	gin.SetMode(gin.TestMode)
	for _, enabled := range []bool{true, false} {
		upstream := &httpUpstreamRecorder{resp: &http.Response{
			StatusCode: http.StatusOK, Header: http.Header{"Content-Type": []string{"application/json"}},
			Body: io.NopCloser(strings.NewReader("{\"output\":[],\"usage\":{\"input_tokens\":1,\"output_tokens\":1}}")),
		}}
		svc := &OpenAIGatewayService{cfg: &config.Config{}, httpUpstream: upstream}
		account := &Account{Platform: PlatformOpenAI, Type: AccountTypeAPIKey, Concurrency: 1,
			Credentials: map[string]any{"api_key": "test-key", "base_url": "https://api.openai.com"},
			Extra:       map[string]any{"openai_passthrough": true, "codex_cli_only": enabled}, RateMultiplier: f64p(1)}
		c, _ := gin.CreateTestContext(httptest.NewRecorder())
		c.Request = httptest.NewRequest(http.MethodPost, "/v1/responses", nil)
		if enabled {
			c.Request.Header.Set("User-Agent", "codex_cli_rs/0.141.0 (x)")
			c.Request.Header.Set("x-codex-installation-id", "test-installation")
		} else {
			c.Request.Header.Set("User-Agent", "curl/8")
		}
		_, err := svc.Forward(context.Background(), c, account, []byte("{\"model\":\"gpt-5.2\",\"input\":\"hello\",\"stream\":false}"))
		require.NoError(t, err)
		require.NotNil(t, upstream.lastReq)
		require.Equal(t, "Bearer test-key", upstream.lastReq.Header.Get("Authorization"))
	}
}

func TestAetherGateAPIKeyClientPolicy(t *testing.T) {
	gin.SetMode(gin.TestMode)
	account := &Account{Platform: PlatformOpenAI, Type: AccountTypeAPIKey, Extra: map[string]any{"codex_cli_only": true}}
	policy := CodexRestrictionPolicy{Whitelist: []openai.AllowedClientEntry{{Originator: "my-client", UAContains: []string{"my-client/"}}}}
	c, _ := gin.CreateTestContext(httptest.NewRecorder())
	c.Request = httptest.NewRequest(http.MethodPost, "/v1/responses", nil)
	c.Request.Header.Set("User-Agent", "my-client/1.0")
	c.Request.Header.Set("originator", "my-client")
	detector := NewOpenAICodexClientRestrictionDetector(nil)
	require.True(t, detector.Detect(c, account, policy, nil).Matched)
	policy.Blacklist = policy.Whitelist
	require.Equal(t, CodexClientRestrictionReasonBlacklisted, detector.Detect(c, account, policy, nil).Reason)
	delete(account.Extra, "codex_cli_only")
	require.False(t, detector.Detect(c, account, policy, nil).Enabled)
	account.Extra["codex_cli_only"] = true
	forced := NewOpenAICodexClientRestrictionDetector(&config.Config{Gateway: config.GatewayConfig{ForceCodexCLI: true}})
	require.Equal(t, CodexClientRestrictionReasonForceCodexCLI, forced.Detect(c, account, policy, nil).Reason)
	account.Extra["codex_cli_only_allow_app_server"] = true
	require.True(t, account.IsCodexCLIOnlyAppServerAllowed())
}

func TestAetherGateAPIKeyRejectsBeforeUpstream(t *testing.T) {
	gin.SetMode(gin.TestMode)
	// All upstream dependencies deliberately nil: a rejected request must never
	// reach credentials, a transport, database, Redis, or a paid provider.
	svc := &OpenAIGatewayService{}
	account := &Account{Platform: PlatformOpenAI, Type: AccountTypeAPIKey, Extra: map[string]any{"codex_cli_only": true, "openai_passthrough": true}}
	body := []byte("{\"model\":\"gpt-5\",\"input\":\"hello\",\"messages\":[]}")
	ctx := context.Background()
	for name, call := range map[string]func(*gin.Context) error{
		"responses": func(c *gin.Context) error { _, err := svc.Forward(ctx, c, account, body); return err },
		"chat": func(c *gin.Context) error {
			_, err := svc.ForwardAsChatCompletions(ctx, c, account, body, "", "")
			return err
		},
		"messages": func(c *gin.Context) error {
			_, err := svc.ForwardAsAnthropic(ctx, c, account, body, "", "")
			return err
		},
		"images":     func(c *gin.Context) error { _, err := svc.ForwardImages(ctx, c, account, body, nil, ""); return err },
		"embeddings": func(c *gin.Context) error { _, err := svc.ForwardEmbeddings(ctx, c, account, body, ""); return err },
		"search":     func(c *gin.Context) error { _, err := svc.ForwardAlphaSearch(ctx, c, account, body); return err },
		"seedance": func(c *gin.Context) error {
			_, err := svc.ForwardSeedance(ctx, c, account, GrokMediaEndpoint(""), "", body)
			return err
		},
		"input_tokens": func(c *gin.Context) error { return svc.ForwardResponsesInputTokens(ctx, c, account, body) },
		"count_tokens": func(c *gin.Context) error { return svc.ForwardCountTokensAsAnthropic(ctx, c, account, body, "") },
	} {
		t.Run(name, func(t *testing.T) {
			recorder := httptest.NewRecorder()
			c, _ := gin.CreateTestContext(recorder)
			c.Request = httptest.NewRequest(http.MethodPost, "/v1/"+name, nil)
			c.Request.Header.Set("User-Agent", "curl/8")
			require.Error(t, call(c))
			require.Equal(t, http.StatusForbidden, recorder.Code)
			require.Contains(t, recorder.Body.String(), "forbidden_error")
		})
	}
	c, _ := gin.CreateTestContext(httptest.NewRecorder())
	c.Request = httptest.NewRequest(http.MethodGet, "/v1/responses", nil)
	err := svc.ProxyResponsesWebSocketFromClient(ctx, c, nil, account, "", body, nil)
	require.Error(t, err)
	require.Contains(t, err.Error(), CodexOfficialClientsOnlyMessage)
}
