#!/bin/bash

# ================= 配置区域 =================
# 远程主机名 (你 SSH Config 里配的那个名字)
REMOTE_HOST="ssmec2"

# 远程存放代码的目录 (请确认这个路径是否正确)
REMOTE_DIR="/home/ec2-user/cross_exchange_arbitrage/"

# ================= 同步逻辑 =================
echo "🚀 开始同步代码到 $REMOTE_HOST ..."

# 使用 rsync 进行增量同步
# -a: 归档模式 (保留权限、时间戳等)
# -v: 显示过程
# -z: 压缩传输 (对慢速网络非常有用)
# --exclude: 排除不需要上传的文件
rsync -avz \
  --exclude '.git/' \
  --exclude '.DS_Store' \
  --exclude '__pycache__/' \
  --exclude '.env' \
  --exclude 'logs/'\
  --exclude 'crarbenv/' \
  --exclude 'env/'\
  --exclude 'deploy.sh' \
  ./ $REMOTE_HOST:$REMOTE_DIR

echo "✅ 同步完成！"

# ================= (可选) 远程重启逻辑 =================
# 如果你想每次同步完自动重启 Bot，可以取消下面几行的注释。
# 但鉴于你在用 tmux，建议还是手动去 tmux 里重启比较安全。

# echo "🔄 正在尝试重启..."
# ssh $REMOTE_HOST "tmux send-keys -t bot C-c 'python3.11 runbot.py --your-params' Enter"
