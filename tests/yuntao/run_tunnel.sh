#!/bin/bash
# 操作端 PC 侧：**反向隧道启动器**。
#
# 作用：把树莓派的 ZMQ 5555(cmd)/5556(obs) 映射到实验室服务器的 loopback，
#       让服务器上的 π0.5 推理进程能「直连」树莓派 —— 因为服务器到树莓派
#       根本没有任何路由（实测 ping 100% 丢包），不走隧道就没法控制机器。
#
# 用法：
#   bash tests/yuntao/run_tunnel.sh              # 正常启动（前台，Ctrl-C 结束）
#   PI_IP=10.42.0.186 bash tests/yuntao/run_tunnel.sh   # 手动指定树莓派 IP
#
# 这个终端必须一直开着：隧道一断，服务器上的评估会立刻 connect() 失败。
#
# ⚠️ 服务器地址走 ~/.ssh/config 的别名 `pi05-server`，不硬编码 IP
#    （这台服务器 IP 换过：10.23.12.237 → 10.23.14.66）。
#    换 IP 时只改 ssh config，本脚本不用动；也可以用
#    SERVER=yuntao@x.x.x.x bash tests/yuntao/run_tunnel.sh 临时覆盖。
set -uo pipefail

PI_USER="${PI_USER:-lab408}"
PI_IP="${PI_IP:-}"
SERVER="${SERVER:-pi05-server}"
PI_HOSTNAME="${PI_HOSTNAME:-lab408.local}"

ok()   { printf '  [OK]   %s\n' "$*"; }
bad()  { printf '  [!!]   %s\n' "$*"; }

# ---------------------------------------------------------------- 1) 找树莓派
if [ -z "$PI_IP" ]; then
  echo "=== 1) 解析树莓派地址（DHCP 会变，所以优先 mDNS）"
  for tool in avahi-resolve getent; do
    command -v "$tool" >/dev/null 2>&1 || continue
    if [ "$tool" = "avahi-resolve" ]; then
      ip=$(avahi-resolve -4 -n "$PI_HOSTNAME" 2>/dev/null | awk '{print $2}')
    else
      ip=$(getent hosts "$PI_HOSTNAME" 2>/dev/null | awk '{print $1}' | head -1)
    fi
    if [ -n "${ip:-}" ]; then
      PI_IP="$ip"; ok "$tool 把 $PI_HOSTNAME 解析成 $PI_IP"; break
    fi
  done
  [ -z "$PI_IP" ] && PI_IP=10.42.0.186 && bad "mDNS 没解析出来，回退到已知地址 $PI_IP"
fi

# ---------------------------------------------------------------- 2) 通不通
echo "=== 2) 检查树莓派是否在线（$PI_IP）"
if ! ping -c1 -W2 "$PI_IP" >/dev/null 2>&1; then
  bad "ping 不通 $PI_IP"
  echo "       可能原因：树莓派没开机 / 没连上本机 WiFi 热点（本机需是 10.42.0.1）"
  echo "       先确认：ip -4 addr show | grep 10.42.0"
  exit 1
fi
ok "ping 通"

# ---------------------------------------------------------------- 3) host 在吗
echo "=== 3) 检查树莓派上的 alohamini_host 是否已在跑（端口 5555/5556）"
missing=0
for p in 5555 5556; do
  if timeout 3 bash -c "echo > /dev/tcp/$PI_IP/$p" 2>/dev/null; then
    ok "端口 $p 已监听"
  else
    bad "端口 $p 没监听"; missing=1
  fi
done
if [ "$missing" = 1 ]; then
  echo
  echo "       请先 SSH 上树莓派把 Host 起起来："
  echo "         ssh $PI_USER@$PI_IP"
  echo "         cd ~/MyProject/lerobot_alohamini"
  echo "         bash tests/yuntao/run_host_right_arm.sh --daemon"
  echo "         ss -ltn | grep -E '5555|5556'"
  echo
  echo "       然后重新运行本脚本。"
  exit 1
fi

# ---------------------------------------------------------------- 4) 服务器端口是否已被占用
# ssh -R 不能复用已被占用的远端端口，会直接报：
#     Error: remote port forwarding failed for listen port 5555
# 最常见原因：**已经有一个隧道在跑**（同一台 PC 的另一个终端，或另一台机器）。
# 这个报错完全不提"已占用"，很容易被误判成"服务器或树莓派坏了"，所以先查。
echo "=== 4) 检查服务器上的 127.0.0.1:5555/5556 是否已被占用"
if ssh -o BatchMode=yes -o ConnectTimeout=10 "$SERVER" \
     'ss -ltn 2>/dev/null | grep -qE "127\.0\.0\.1:555[56]"' 2>/dev/null; then
  echo "  [!!] 服务器上已经有进程在监听 5555/5556 —— 隧道会建不起来。"
  echo
  echo "       最可能：已经有一个反向隧道在跑。在本机查："
  echo "         pgrep -af 'ssh -N'"
  echo "       关掉它（用查到的 PID）："
  echo "         kill <pid>"
  echo "       （如果是另一台机器开的隧道，要去那台机器上关）"
  echo
  echo "       确认端口已释放后，重新运行本脚本。"
  exit 1
fi
echo "  [OK] 服务器端口空闲"

# ---------------------------------------------------------------- 5) 开隧道
echo "=== 5) 建立反向隧道到 $SERVER"
echo "      $PI_IP:5555 -> 服务器 127.0.0.1:5555   （动作下发）"
echo "      $PI_IP:5556 -> 服务器 127.0.0.1:5556   （观测回传，含图像）"
echo
echo "  隧道起来后，在服务器上跑："
echo "      cd ~/MyProject/2_Alohamini2/lerobot_alohamini"
echo "      SKIP_CHECKS=1 N_EPISODES=10 bash tests/yuntao/run_pi05_eval.sh"
echo
echo "  （本终端请保持开着；Ctrl-C 结束隧道）"
echo

exec ssh -N \
  -o ServerAliveInterval=15 -o ServerAliveCountMax=4 \
  -o ExitOnForwardFailure=yes \
  -R 5555:"$PI_IP":5555 \
  -R 5556:"$PI_IP":5556 \
  "$SERVER"
