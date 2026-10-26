FROM node:24.18.0-bookworm AS build

WORKDIR /app

COPY package*.json ./
RUN test "$(node --version)" = "v24.18.0" \
    && test "$(npm --version)" = "11.16.0" \
    && npm ci --no-audit --no-fund --strict-allow-scripts

COPY tsconfig.json ./
COPY src ./src
COPY migrations ./migrations

RUN npm run build && npm prune --omit=dev --no-audit --strict-allow-scripts

FROM node:24.18.0-bookworm

WORKDIR /app

# 保留 Playwright 官方镜像使用的非 root 运行用户，确保 Chrome profile / HOME 可写。
RUN groupadd --gid 10001 pwuser \
    && useradd --uid 10001 --gid pwuser --create-home --shell /usr/sbin/nologin pwuser

RUN npm install --global npm@11.16.0 \
    && test "$(node --version)" = "v24.18.0" \
    && test "$(npm --version)" = "11.16.0"

# 安装 Xvfb + google-chrome-stable + Python + patchright：
# - Xvfb：让线上 Linux Docker 也能运行有头 Chrome（headless 下 Baxia 风控识别率极高）
# - google-chrome-stable：Playwright 自带 Chrome for Testing 149 在 Docker headed 模式下崩溃
#   (SIGTRAP / chrome_crashpad_handler: --database is required)，改用系统 Chrome 通过
#   channel:'chrome' 调用，是 Playwright 官方推荐方案
# - python3 + patchright：商机发掘搜索的 Python 兜底脚本（goofishSearch.py）依赖 patchright
#   反检测分支（自动清理 CDP 痕迹），是 Node Playwright 触发 Baxia 风控后的关键兜底。
#   注意：仅用于商机发掘搜索（goofishSearch.ts），不影响滑块求解（sliderSolver.ts 仍走 Node Playwright）。
RUN sed -i 's|http://deb.debian.org/debian|https://mirrors.tuna.tsinghua.edu.cn/debian|g; s|http://deb.debian.org/debian-security|https://mirrors.tuna.tsinghua.edu.cn/debian-security|g' /etc/apt/sources.list.d/debian.sources \
    && apt-get -o Acquire::Retries=5 -o Acquire::https::Timeout=30 update \
    && apt-get -o Acquire::Retries=5 -o Acquire::https::Timeout=30 install -y --no-install-recommends \
        xvfb \
        xauth \
        xdotool \
        python3 \
        python3-pip \
        python3-venv \
        curl \
        ca-certificates \
        fonts-liberation \
        libasound2 \
        libatk-bridge2.0-0 \
        libatk1.0-0 \
        libatspi2.0-0 \
        libcups2 \
        libdbus-1-3 \
        libdrm2 \
        libgbm1 \
        libgtk-3-0 \
        libnspr4 \
        libnss3 \
        libxcomposite1 \
        libxdamage1 \
        libxfixes3 \
        libxkbcommon0 \
        libxrandr2 \
    && curl --fail --location --show-error --retry 3 --connect-timeout 20 --max-time 300 \
        https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb \
        --output /tmp/chrome.deb \
    && apt-get install -y --no-install-recommends /tmp/chrome.deb \
    && rm -rf /var/lib/apt/lists/* /tmp/chrome.deb \
    && python3 -m pip install --no-cache-dir --break-system-packages \
        -i https://pypi.tuna.tsinghua.edu.cn/simple \
        --trusted-host pypi.tuna.tsinghua.edu.cn \
        patchright

COPY --from=build --chown=pwuser:pwuser /app/package.json /app/package-lock.json ./
COPY --from=build --chown=pwuser:pwuser /app/node_modules ./node_modules
COPY --from=build --chown=pwuser:pwuser /app/dist ./dist
COPY --from=build --chown=pwuser:pwuser /app/migrations ./migrations

# 复制 Python 兜底脚本（goofishSearch.py + sliderSolve.py）到镜像根目录
# 这两个脚本仅在商机发掘搜索触发 Baxia 风控时被调用，不影响滑块求解模块
COPY --chown=pwuser:pwuser goofishSearch.py sliderSolve.py ./

ENV NODE_ENV=production
# 保留 HEADLESS=true 作为滑块求解（sliderSolver.ts）的默认配置。
# 滑块求解在该模式下成功率非常高，严禁改动此环境变量。
# 商机发掘搜索（goofishSearch.ts）不读取此变量，自行根据 DISPLAY 决定 headless，
# 以便在 Xvfb 提供显示器时使用有头模式降低 Baxia 风控触发率。
ENV HEADLESS=true
ENV PORT=3001
# 设置 HOME 为可写目录：pwuser 默认 HOME=/home/pwuser 不可写，
# Chrome 150 启动 chrome_crashpad_handler 时需要可写 HOME 作为 crash database 默认目录，
# 否则会因找不到 database 触发 SIGTRAP 崩溃（即便已通过 --disable-crashpad 禁用，仍需 HOME 可写
# 以避免 Chrome 其他子模块因权限问题失败）
ENV HOME=/tmp
USER pwuser

# 用 xvfb-run 包装启动命令，自动启动 Xvfb 虚拟显示器并设置 DISPLAY 环境变量
# -a: 自动选择可用的 display number，避免冲突
# -s: 传给 Xvfb 的参数，-screen 0 1920x1080x24 提供足够分辨率供 Chrome 渲染
# 2026-08-01 优化：屏幕从 1280x800 增大到 1920x1080，配合 xdotool 系统级鼠标拖动
# 原因：sliderSolve.py 的 WINDOW_WIDTH=1366，原 Xvfb 1280 宽度不够，浏览器窗口超出屏幕，
#       xdotool 无法操作屏幕外区域。1920x1080 完全包含 1366x768 浏览器窗口。
CMD ["/bin/bash", "-lc", "set -euo pipefail; (while true; do find /tmp -maxdepth 1 -mindepth 1 \\( -name 'chrome-slider-temp-*' -o -name 'chrome-slider-warm-*' -o -name 'chrome-slider-seed-*' -o -name 'playwright-slider-*' \\) -mmin +30 -exec rm -rf {} + 2>/dev/null || true; find /tmp -maxdepth 1 -mindepth 1 \\( -name '.com.google.Chrome.*' -o -name 'com.google.Chrome.*' \\) -mmin +30 -exec rm -rf {} + 2>/dev/null || true; sleep 300; done) & exec xvfb-run -a -s '-screen 0 1920x1080x24 -ac' node dist/server.js"]
