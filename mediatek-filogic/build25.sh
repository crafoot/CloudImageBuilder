#!/bin/bash
set -euo pipefail

CUSTOM_PACKAGES="${CUSTOM_PACKAGES:-}"
source shell/apk-custom-packages.sh

# daed and Nikki are built from pinned official sources in the workflow with
# the matching ImmortalWrt 25.12.1 SDK. Only the expected APKs are accepted.
if [[ " $CUSTOM_PACKAGES " == *" luci-i18n-daed-zh-cn "* ]] ||
   [[ " $CUSTOM_PACKAGES " == *" luci-i18n-nikki-zh-cn "* ]]; then
  OFFICIAL_PACKAGES_DIR="/home/build/immortalwrt/official-proxy-packages"
  if [[ ! -f "$OFFICIAL_PACKAGES_DIR/SHA256SUMS" ]]; then
    echo "Official proxy package checksums are missing"
    exit 1
  fi

  (
    cd "$OFFICIAL_PACKAGES_DIR"
    sha256sum --check SHA256SUMS
  )

  mapfile -t proxy_apks < <(find "$OFFICIAL_PACKAGES_DIR" -maxdepth 1 -type f -name '*.apk' -print)
  if [[ "${#proxy_apks[@]}" -ne 8 ]]; then
    echo "Expected exactly eight verified daed and Nikki APKs, found ${#proxy_apks[@]}"
    exit 1
  fi

  for pattern in \
    'nikki-[0-9]*.apk' \
    'luci-app-nikki-*.apk' \
    'luci-i18n-nikki-zh-cn-*.apk' \
    'daed-[0-9]*.apk' \
    'daed-geoip-*.apk' \
    'daed-geosite-*.apk' \
    'luci-app-daed-*.apk' \
    'luci-i18n-daed-zh-cn-*.apk'; do
    mapfile -t matches < <(find "$OFFICIAL_PACKAGES_DIR" -maxdepth 1 -type f -name "$pattern" -print)
    if [[ "${#matches[@]}" -ne 1 ]]; then
      echo "Expected exactly one package matching $pattern, found ${#matches[@]}"
      exit 1
    fi
  done

  mkdir -p /home/build/immortalwrt/packages
  cp "${proxy_apks[@]}" /home/build/immortalwrt/packages/
  echo "Official daed and Nikki packages verified and staged"
fi



# yml 传入的路由器型号 PROFILE
echo "Building for profile: $PROFILE"

echo "Include Docker: $INCLUDE_DOCKER"
echo "Create pppoe-settings"
mkdir -p  /home/build/immortalwrt/files/etc/config

# 创建pppoe配置文件 yml传入pppoe变量————>pppoe-settings文件
cat << EOF > /home/build/immortalwrt/files/etc/config/pppoe-settings
enable_pppoe=${ENABLE_PPPOE}
pppoe_account=${PPPOE_ACCOUNT}
pppoe_password=${PPPOE_PASSWORD}
EOF

echo "cat pppoe-settings"
cat /home/build/immortalwrt/files/etc/config/pppoe-settings

# 输出调试信息
echo "$(date '+%Y-%m-%d %H:%M:%S') - Starting build process..."


# 定义所需安装的包列表 下列插件你都可以自行删减
PACKAGES=""
PACKAGES="$PACKAGES curl luci luci-i18n-base-zh-cn"
PACKAGES="$PACKAGES luci-i18n-firewall-zh-cn"
PACKAGES="$PACKAGES luci-theme-argon"
PACKAGES="$PACKAGES luci-app-argon-config"
PACKAGES="$PACKAGES luci-i18n-argon-config-zh-cn"
PACKAGES="$PACKAGES luci-i18n-diskman-zh-cn"
PACKAGES="$PACKAGES luci-i18n-package-manager-zh-cn"
PACKAGES="$PACKAGES luci-i18n-ttyd-zh-cn"
PACKAGES="$PACKAGES openssh-sftp-server"
# USB RNDIS tethering；自动带入 kmod-usb-net 和 kmod-usb-net-cdc-ether
PACKAGES="$PACKAGES kmod-usb-net-rndis"
# 文件管理器
PACKAGES="$PACKAGES luci-i18n-filemanager-zh-cn"


# 第三方软件包 合并
# ======== shell/apk-custom-packages.sh =======
PACKAGES="$PACKAGES $CUSTOM_PACKAGES"


# 判断是否需要编译 Docker 插件
if [ "$INCLUDE_DOCKER" = "yes" ]; then
    PACKAGES="$PACKAGES luci-i18n-dockerman-zh-cn"
    echo "Adding package: luci-i18n-dockerman-zh-cn"
fi

# 若构建openclash 则添加内核
if echo "$PACKAGES" | grep -q "luci-app-openclash"; then
    echo "✅ 已选择 luci-app-openclash，添加 openclash core"
    mkdir -p files/etc/openclash/core
    # Download clash_meta
    META_URL="https://raw.githubusercontent.com/vernesong/OpenClash/core/master/meta/clash-linux-arm64.tar.gz"
    wget -qO- $META_URL | tar xOvz > files/etc/openclash/core/clash_meta
    chmod +x files/etc/openclash/core/clash_meta
    # Download GeoIP and GeoSite
    wget -q https://github.com/Loyalsoldier/v2ray-rules-dat/releases/latest/download/geoip.dat -O files/etc/openclash/GeoIP.dat
    wget -q https://github.com/Loyalsoldier/v2ray-rules-dat/releases/latest/download/geosite.dat -O files/etc/openclash/GeoSite.dat
    # Download latest openclash Client
    URL=$(curl -s https://api.github.com/repos/vernesong/OpenClash/releases/latest \
      | grep "browser_download_url.*apk" \
      | head -n1 \
      | cut -d '"' -f 4)
    echo "OpenClash latest apk: $URL"
    wget "$URL" -P /home/build/immortalwrt/packages/
else
    echo "⚪️ 未选择 luci-app-openclash"
fi


# 构建镜像
echo "$(date '+%Y-%m-%d %H:%M:%S') - Building image with the following packages:"
echo "$PACKAGES"

make image PROFILE=$PROFILE PACKAGES="$PACKAGES" FILES="/home/build/immortalwrt/files"

if [ $? -ne 0 ]; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') - Error: Build failed!"
    exit 1
fi

echo "$(date '+%Y-%m-%d %H:%M:%S') - Build completed successfully."
