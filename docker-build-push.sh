#!/bin/bash
# =============================================================================
# Docker 镜像构建 & 推送脚本
# 用法:
#   ./docker-build-push.sh                    # 构建并推送
#   ./docker-build-push.sh --build-only       # 仅构建
#   ./docker-build-push.sh --push-only        # 仅推送
#   ./docker-build-push.sh --no-cache         # 无缓存构建
#   ./docker-build-push.sh --tag v1.0.0       # 指定版本号
# =============================================================================

set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; CYAN='\033[0;36m'; NC='\033[0m'
log_info()  { echo -e "${BLUE}[INFO]${NC}  $*"; }
log_ok()    { echo -e "${GREEN}[OK]${NC}    $*"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
log_err()   { echo -e "${RED}[ERROR]${NC} $*"; }

# ---- 默认值 ----
BUILD_ONLY=false
PUSH_ONLY=false
NO_CACHE=""
CUSTOM_TAG=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --build-only) BUILD_ONLY=true ;;
        --push-only)  PUSH_ONLY=true ;;
        --no-cache)   NO_CACHE="--no-cache" ;;
        --tag)        CUSTOM_TAG="$2"; shift ;;
        --help|-h)
            echo "用法: $0 [选项]"
            echo "  --build-only    仅构建"
            echo "  --push-only     仅推送"
            echo "  --no-cache      无缓存构建"
            echo "  --tag <TAG>     指定版本号"
            exit 0 ;; 
        *) log_err "未知参数: $1"; exit 1 ;;
    esac
    shift
done

# ---- 配置 ----
REGISTRY="${DOCKER_REGISTRY:-calciumion}"
IMAGE_NAME="${DOCKER_IMAGE_NAME:-any-auto-register}"
PLATFORM="${DOCKER_PLATFORM:-linux/amd64}"
CAMOUFOX_VERSION="${CAMOUFOX_VERSION:-135.0.1}"
CAMOUFOX_RELEASE="${CAMOUFOX_RELEASE:-beta.24}"

# ---- 生成 TAG ----
if [ -n "$CUSTOM_TAG" ]; then
    TAG="$CUSTOM_TAG"
elif [ -n "${DOCKER_TAG:-}" ]; then
    TAG="$DOCKER_TAG"
else
    GIT_HASH=$(git rev-parse --short HEAD 2>/dev/null || echo "unknown")
    TAG="$(date +%Y%m%d-%H%M%S)-${GIT_HASH}"
fi

FULL_IMAGE="${REGISTRY}/${IMAGE_NAME}:${TAG}"
LATEST_IMAGE="${REGISTRY}/${IMAGE_NAME}:latest"

log_info "镜像: ${FULL_IMAGE}"
log_info "平台: ${PLATFORM}"
echo ""

# ---- 构建 ----
if [ "$PUSH_ONLY" = false ]; then
    T0=$(date +%s)
    log_info "开始构建..."

    docker buildx build \
        --platform "$PLATFORM" \
        --build-arg CAMOUFOX_VERSION="$CAMOUFOX_VERSION" \
        --build-arg CAMOUFOX_RELEASE="$CAMOUFOX_RELEASE" \
        -t "$FULL_IMAGE" \
        -t "$LATEST_IMAGE" \
        $NO_CACHE \
        --load \
        -f Dockerfile \
        .

    T1=$(date +%s)
    log_ok "构建完成 (${T1}-${T0}s)"
fi

# ---- 推送 ----
if [ "$BUILD_ONLY" = false ]; then
    log_info "推送: $FULL_IMAGE"
    docker push "$FULL_IMAGE"
    log_ok "推送完成: $FULL_IMAGE"

    log_info "推送: $LATEST_IMAGE"
    docker push "$LATEST_IMAGE"
    log_ok "推送完成: $LATEST_IMAGE"
fi

echo ""
log_info "服务器端拉取:"
echo -e "  ${GREEN}docker pull ${LATEST_IMAGE}${NC}"
echo -e "  ${GREEN}cd ~/auto_reg && docker compose pull && docker compose up -d${NC}"
echo ""
log_ok "全部完成!"
