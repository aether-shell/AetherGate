package service

import (
	"context"
	"errors"
	"net/http"

	"github.com/gin-gonic/gin"
)

// Require the original client identity before protocol conversion or upstream I/O.
func (s *OpenAIGatewayService) enforceClientRestriction(ctx context.Context, c *gin.Context, account *Account, body []byte) error {
	result := s.detectCodexClientRestriction(c, account, body)
	logCodexCLIOnlyDetection(ctx, c, account, getAPIKeyIDFromContext(c), result, body)
	if !result.Enabled || result.Matched {
		return nil
	}
	MarkOpsClientBusinessLimited(c, OpsClientBusinessLimitedReasonLocalPolicyDenied)
	c.JSON(http.StatusForbidden, gin.H{"error": gin.H{
		"type": "forbidden_error", "message": CodexClientRestrictionMessage(result),
	}})
	return errors.New("codex_cli_only restriction: client is not allowed")
}
