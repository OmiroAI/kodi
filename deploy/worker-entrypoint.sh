#!/bin/sh
set -eu
if [ -S /run/kodi-proxy/proxy.sock ]; then
  socat TCP-LISTEN:3128,bind=127.0.0.1,reuseaddr,fork UNIX-CONNECT:/run/kodi-proxy/proxy.sock &
  export HTTPS_PROXY=http://127.0.0.1:3128
  export HTTP_PROXY=http://127.0.0.1:3128
  export https_proxy="$HTTPS_PROXY"
  export http_proxy="$HTTP_PROXY"
  export NODE_USE_ENV_PROXY=1
fi
exec "$@"
