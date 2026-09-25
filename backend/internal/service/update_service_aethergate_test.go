//go:build unit

package service

import (
	"context"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestAetherGateManagedUpdates(t *testing.T) {
	for _, buildType := range []string{"", "source", "release", "aethergate"} {
		t.Run(buildType, func(t *testing.T) {
			// nil 依赖证明拒绝发生在缓存、GitHub 下载及磁盘访问之前。
			svc := NewUpdateService(nil, nil, "0.2.8-aethergate.1", buildType)
			ctx := context.Background()
			for _, force := range []bool{false, true} {
				info, err := svc.CheckUpdate(ctx, force)
				require.NoError(t, err)
				require.True(t, info.Managed)
				require.Equal(t, "aethergate", info.BuildType)
				require.Equal(t, "https://github.com/aether-shell/AetherGate", info.Repository)
				require.False(t, info.HasUpdate)
				require.Empty(t, info.LatestVersion)
				require.Nil(t, info.ReleaseInfo)
			}
			require.ErrorIs(t, svc.PerformUpdate(ctx), ErrSelfUpdateDisabled)
			require.ErrorIs(t, svc.Rollback(), ErrSelfUpdateDisabled)
			require.ErrorIs(t, svc.RollbackToVersion(ctx, "0.2.7"), ErrSelfUpdateDisabled)
			require.ErrorIs(t, svc.applyReleaseAssets(ctx, []Asset{{DownloadURL: "https://github.com/official"}}), ErrSelfUpdateDisabled)
			_, err := svc.ListRollbackVersions(ctx)
			require.ErrorIs(t, err, ErrSelfUpdateDisabled)
		})
	}
}
