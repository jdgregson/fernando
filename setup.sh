#!/bin/bash

# Setup script for Fernando
# Installs all dependencies on a fresh Ubuntu Server 24.04 install
# Usage: sudo ./setup.sh [cloudflare-tunnel-token]

TZ="America/Los_Angeles"
APP="fernando"
SYSBOX_VERSION="0.6.7"

CLOUDFLARED_TOKEN=$1

# Green and red echo
gecho() { echo -e "\033[1;32m$1\033[0m"; }
recho() { echo -e "\033[1;31m$1\033[0m"; }

if [ "$(id -u)" -ne 0 ]; then
    recho "ERROR: This script must be run as root"
    exit 1
fi

if [ ! -f "/etc/lsb-release" ] || [ -z "$(grep '24.04' /etc/lsb-release)" ]; then
    recho "ERROR: $APP only supports Ubuntu Server 24.04"
    exit 1
fi

FERNANDO_USER="fernando"
FERNANDO_HOME="/home/$FERNANDO_USER"
INSTALL_DIR="$FERNANDO_HOME/$APP"

gecho "Setting time zone to $TZ..."
timedatectl set-timezone "$TZ"
export DEBIAN_FRONTEND=noninteractive

# Load secrets if available
if [ -f "/root/secrets" ]; then
    source /root/secrets
fi

# Add Docker's repository
gecho "Adding Docker repository..."
apt-get update
apt-get install --yes ca-certificates curl
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu \
  $(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}") stable" | \
  tee /etc/apt/sources.list.d/docker.list > /dev/null
apt-get update

# Install updates and dependencies
gecho "Installing updates and dependencies..."
apt-get remove needrestart --yes
apt-get upgrade --yes
apt-get install --yes \
    python3 \
    python3-pip \
    python3-venv \
    unzip \
    unattended-upgrades \
    vim \
    git \
    tmux \
    nginx \
    at \
    openssl \
    docker-ce \
    docker-ce-cli \
    containerd.io \
    docker-buildx-plugin \
    docker-compose-plugin

# Install Sysbox container runtime
gecho "Installing Sysbox v${SYSBOX_VERSION}..."
SYSBOX_DEB="sysbox-ce_${SYSBOX_VERSION}.linux_amd64.deb"
wget -q "https://downloads.nestybox.com/sysbox/releases/v${SYSBOX_VERSION}/${SYSBOX_DEB}" -O "/tmp/${SYSBOX_DEB}"
apt-get install --yes "/tmp/${SYSBOX_DEB}"
rm -f "/tmp/${SYSBOX_DEB}"

# Create fernando user
gecho "Creating and configuring user $FERNANDO_USER..."
if [ ! -d "$FERNANDO_HOME" ]; then
    useradd -m -s /bin/bash "$FERNANDO_USER"
fi
usermod -aG docker "$FERNANDO_USER"

echo "$FERNANDO_USER ALL=(ALL) NOPASSWD: /bin/systemctl restart fernando, /bin/systemctl stop fernando, /bin/systemctl start fernando, /sbin/reboot" | tee /etc/sudoers.d/99-$FERNANDO_USER-fernando

# Disable system nginx - Fernando manages its own nginx process
systemctl stop nginx
systemctl disable nginx

# Install awscli
gecho "Installing awscli..."
INSTALL_TMP=$(mktemp -d)
curl -s "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o "$INSTALL_TMP/awscliv2.zip"
unzip -q "$INSTALL_TMP/awscliv2.zip" -d "$INSTALL_TMP"
"$INSTALL_TMP/aws/install"
rm -rf "$INSTALL_TMP"

# Configure AWS credentials if provided
if [ -n "$AWS_ACCESS_KEY_ID" ]; then
    gecho "Configuring AWS credentials..."
    mkdir -p "$FERNANDO_HOME/.aws"
    cat > "$FERNANDO_HOME/.aws/config" << EOF
[default]
region = ${AWS_REGION:-us-west-2}
output = json
EOF
    cat > "$FERNANDO_HOME/.aws/credentials" << EOF
[default]
aws_access_key_id = $AWS_ACCESS_KEY_ID
aws_secret_access_key = $AWS_SECRET_ACCESS_KEY
EOF
    chmod 600 "$FERNANDO_HOME/.aws/credentials"
    chown -R "$FERNANDO_USER:$FERNANDO_USER" "$FERNANDO_HOME/.aws"
fi

# Install kiro-cli
gecho "Installing kiro-cli..."
sudo -u "$FERNANDO_USER" bash -c 'curl -fsSL https://cli.kiro.dev/install | bash'

# Clone Fernando
gecho "Installing Fernando..."
if [ ! -d "$INSTALL_DIR" ]; then
    sudo -u "$FERNANDO_USER" git clone https://github.com/jdgregson/fernando.git "$INSTALL_DIR"
fi

# Ensure cron and at daemons are running
gecho "Enabling cron and atd..."
systemctl enable cron
systemctl start cron
systemctl enable atd
systemctl start atd

# Enable unattended upgrades
gecho "Configuring unattended upgrades..."
dpkg-reconfigure -f noninteractive unattended-upgrades

# Set up Fernando systemd service
gecho "Configuring Fernando service..."
cat > /etc/systemd/system/fernando.service << EOF
[Unit]
Description=Fernando terminal manager
After=network.target docker.service
Requires=docker.service

[Service]
Type=forking
User=$FERNANDO_USER
WorkingDirectory=$INSTALL_DIR
ExecStart=$INSTALL_DIR/scripts/start.sh
ExecStop=$INSTALL_DIR/scripts/stop.sh
RemainAfterExit=yes
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable fernando

# Install Cloudflared
gecho "Installing cloudflared..."
mkdir -p --mode=0755 /usr/share/keyrings
curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg | tee /usr/share/keyrings/cloudflare-main.gpg >/dev/null
echo 'deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared any main' | tee /etc/apt/sources.list.d/cloudflared.list
apt-get update
apt-get install --yes cloudflared

# Set up Cloudflare tunnel if token provided
if [ -n "$CLOUDFLARED_TOKEN" ]; then
    gecho "Configuring Cloudflare tunnel..."
    cloudflared service uninstall 2>/dev/null
    cloudflared service install "$CLOUDFLARED_TOKEN"
fi

# Symlink ~/Desktop, ~/Downloads, ~/Documents to the Kasm container so host and VM share them
gecho "Linking shared directories to Kasm container..."
for dir in Desktop Downloads Documents; do
    mkdir -p "$INSTALL_DIR/data/desktop/$dir"
    rm -rf "$FERNANDO_HOME/$dir"
    ln -s "$INSTALL_DIR/data/desktop/$dir" "$FERNANDO_HOME/$dir"
done

# Fix permissions
gecho "Restoring permissions..."
chown -R "$FERNANDO_USER:$FERNANDO_USER" "$FERNANDO_HOME"

# Build the Kasm desktop container
gecho "Building Kasm desktop container..."
sudo -u "$FERNANDO_USER" bash -c "cd $INSTALL_DIR && docker compose build"

# Pull SilverBullet image
gecho "Pulling SilverBullet image..."
sudo -u "$FERNANDO_USER" docker pull zefhemel/silverbullet@sha256:6c36ff15f2230dbe3bca7e5d0c85a59c7dc831ce694517850ed5797775824d71

# Symlink SilverBullet defaults into notes data directory
mkdir -p "$INSTALL_DIR/data/notes"
cp -n "$INSTALL_DIR/silverbullet/SETTINGS.md" "$INSTALL_DIR/data/notes/SETTINGS.md"

gecho ""
gecho "========================================="
gecho "  Fernando installation complete!"
gecho "========================================="
gecho ""
gecho "To start Fernando:"
gecho "  systemctl start fernando"
gecho ""
gecho "Access at: http://localhost:8080"
gecho ""
gecho "To configure, copy and edit the config file:"
gecho "  cp $INSTALL_DIR/config.example $INSTALL_DIR/config"
gecho ""
if [ -n "$1" ]; then
    gecho "Cloudflare tunnel configured."
fi

# Set up daily update cron job (6:00 AM)
gecho "Configuring daily update cron job..."
CRON_LINE="0 6 * * * $INSTALL_DIR/scripts/update-kiro.sh >> /tmp/fernando-update-kiro.log 2>&1; $INSTALL_DIR/scripts/update-desktop.sh >> /tmp/fernando-update-desktop.log 2>&1"
(sudo -u "$FERNANDO_USER" crontab -l 2>/dev/null | grep -v 'update-kiro\|update-desktop'; echo "$CRON_LINE") | sudo -u "$FERNANDO_USER" crontab -

# Install global Kiro steering file
gecho "Installing Kiro steering file..."
sudo -u "$FERNANDO_USER" mkdir -p "$FERNANDO_HOME/.kiro/steering"

# Install Jupyter custom theme
gecho "Installing Jupyter custom theme..."
jupyter_custom_dst="$FERNANDO_HOME/.jupyter/custom"
if [ -L "$jupyter_custom_dst" ]; then
    gecho "Jupyter custom symlink already exists, skipping."
elif [ -d "$jupyter_custom_dst" ]; then
    gecho "WARNING: $jupyter_custom_dst is a regular directory. Remove it and re-run setup to use the repo copy."
else
    sudo -u "$FERNANDO_USER" mkdir -p "$FERNANDO_HOME/.jupyter"
    sudo -u "$FERNANDO_USER" ln -s "$INSTALL_DIR/jupyter/custom" "$jupyter_custom_dst"
    gecho "Symlinked $jupyter_custom_dst -> $INSTALL_DIR/jupyter/custom"
fi
instructions_src="$INSTALL_DIR/instructions.md"
instructions_dst="$FERNANDO_HOME/.kiro/steering/instructions.md"
if [ -L "$instructions_dst" ]; then
    gecho "Instructions symlink already exists at $instructions_dst, skipping."
elif [ -f "$instructions_dst" ]; then
    gecho "WARNING: $instructions_dst is a regular file, not a symlink. Remove it and re-run setup to use the repo copy."
else
    sudo -u "$FERNANDO_USER" ln -s "$instructions_src" "$instructions_dst"
    gecho "Symlinked $instructions_dst -> $instructions_src"
fi

