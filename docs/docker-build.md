# Docker 镜像构建：已知问题与排障

本文件记录 2026-11-28 在弱网/国内网络构建 Octop 生产镜像时踩到的问题，供后续构建和部署参考。

## 常见症状

1. `COPY --from=ghcr.io/astral-sh/uv:0.7 /uv /uvx /bin/` 报
   `TLS handshake timeout`，构建在第一个 `COPY --from` 就失败。
2. 绕过 uv 镜像后，`uv sync` 在 PyPI 下载阶段报
   `error decoding response body` / `stream error ...`（曾在
   `aliyun-python-sdk-core` 上出现），重试时所有包重新下载。
3. 后台构建“看起来成功”，实际 `docker build` 已失败：命令末尾的
   `tail` 覆盖了非零退出码，任务状态显示 0。
4. 构建完成后 `docker images` 的 `CreatedAt` 没有变化，容器仍是旧镜像。

## 根因

- **ghcr.io / pypi.org 访问不稳定**：uv 镜像和 Python 依赖都走公网，
  下载中断会直接失败。
- **uv 缓存挂载没有生效**：Dockerfile 先设置 `ENV HOME=/data`，uv 默认缓存目录
  是 `$HOME/.cache/uv`（即 `/data/.cache/uv`），而构建挂载写的是
  `/root/.cache/uv`。挂载点与 uv 实际使用的目录不一致，导致每次重试都重新下载。
  现在 Dockerfile 显式设置 `UV_CACHE_DIR=/root/.cache/uv`，与缓存挂载一致。
- **退出码被掩盖**：后台命令写成
  `docker build ... > log 2>&1; echo "..."; tail -5 log`，shell 最终返回的是
  `tail` 的 0，而不是 `docker build` 的退出码。

## 推荐构建方式

网络受限时优先使用镜像源：

```bash
PIP_INDEX_URL=https://mirrors.cloud.tencent.com/pypi/simple \
PIP_TRUSTED_HOST=mirrors.cloud.tencent.com \
NPM_REGISTRY=https://mirrors.cloud.tencent.com/npm/ \
APT_MIRROR=mirrors.cloud.tencent.com \
bash docker/docker_build.sh octop:miniapp
```

构建前后都要确认结果，不要只看“命令结束”：

```bash
bash docker/docker_build.sh octop:miniapp > /tmp/octop-build.log 2>&1
rc=$?
tail -40 /tmp/octop-build.log
exit $rc                      # 后台任务也必须传播真实退出码
docker images octop:miniapp --format '{{.ID}}\t{{.CreatedAt}}'
```

`docker_build.sh` 已支持 `PIP_INDEX_URL` / `PIP_TRUSTED_HOST` / `NPM_REGISTRY` /
`APT_MIRROR` / `NODE_MAX_OLD_SPACE_SIZE`；不要把 `-m` 等 pytest 参数混进构建。

## ghcr.io 拉不到 uv 时

先尝试预拉取：

```bash
docker pull ghcr.io/astral-sh/uv:0.7
```

仍失败时，可用已有镜像里的 uv 作为构建来源：把 Dockerfile 的
`COPY --from=ghcr.io/astral-sh/uv:0.7 /uv /uvx /bin/` 临时改成
`COPY --from=<本地已有镜像> /bin/uv /bin/uvx /bin/`。不要把这个临时改动提交。

## 只改代码、依赖未变时的快速发布

当 `uv.lock` 没变、只是 `src/` 有改动时，可以先做一个基于上一版镜像的热修复镜像，
避免重新安装全部依赖：

```bash
# 上一版镜像已包含 /app/.venv；octop 从 /app/src 导入代码
docker tag octop:miniapp octop:miniapp-prev 2>/dev/null || true
mkdir -p /tmp/octop-hotfix-context
cp -a src /tmp/octop-hotfix-context/src          # 含 host 已构建的 src/octop/dashboard
printf 'FROM octop:miniapp-prev\nCOPY src/ /app/src/\n' \
  > /tmp/octop-hotfix-context/Dockerfile
docker build -f /tmp/octop-hotfix-context/Dockerfile -t octop:miniapp /tmp/octop-hotfix-context
```

注意：

- `.dockerignore` 排除了 `src/octop/dashboard`；快速发布必须在**临时构建上下文**里
  放入 host 已构建的前端（先 `make build-frontend`），否则页面仍是旧版。
- 依赖有变化时必须走完整 `docker_build.sh`，不要用热修复镜像。
- 重建容器后，几何内核等容器内进程不会自动回来，需要显式启动：
  `docker exec octop sh /data/.octop/agents/QVKHGP/generated/start_geometry_kernel.sh`
- 重建后确认健康：`docker compose -f /home/ubuntu/octop-compose.yml ps`，或
  `docker inspect --format '{{.State.Health.Status}}' octop`。

## 构建前检查清单

- [ ] 使用镜像源（国内网络）：`APT_MIRROR`、`PIP_INDEX_URL`、`NPM_REGISTRY`。
- [ ] Dockerfile 保留 `UV_CACHE_DIR=/root/.cache/uv`（与 `--mount=type=cache` 一致）。
- [ ] 后台构建传播真实退出码，并检查 `docker images` 的 `CreatedAt`/镜像 ID。
- [ ] 依赖变更走完整构建；仅源码变更可用“旧镜像 + 覆盖 src”快速发布。
- [ ] 重建容器后启动容器内内核，并验证 `/api/health` 与 8848/8849。
