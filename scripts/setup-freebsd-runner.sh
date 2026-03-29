#!/bin/sh
# setup-freebsd-runner.sh
# Sets up a GitHub Actions self-hosted runner on FreeBSD using github-act-runner
#
# Prerequisites:
#   - FreeBSD 14.x or 15.x
#   - Root access
#   - A GitHub personal access token or runner registration token
#
# Usage:
#   chmod +x setup-freebsd-runner.sh
#   sudo ./setup-freebsd-runner.sh <GITHUB_OWNER/REPO> <RUNNER_TOKEN>
#
# To generate a runner token:
#   Go to: Settings > Actions > Runners > New self-hosted runner
#   Or use: gh api -X POST repos/OWNER/REPO/actions/runners/registration-token --jq .token

set -e

# --- Configuration ---
RUNNER_VERSION="0.13.0"
RUNNER_USER="github-runner"
RUNNER_HOME="/home/${RUNNER_USER}"
RUNNER_DIR="${RUNNER_HOME}/actions-runner"
RUNNER_LABELS="freebsd,self-hosted"
RUNNER_NAME="$(hostname)-freebsd"

# --- Argument parsing ---
if [ $# -lt 2 ]; then
    echo "Usage: $0 <owner/repo-or-org> <runner-registration-token> [runner-name]"
    echo ""
    echo "Examples:"
    echo "  $0 myorg/myrepo AXXXXXXXXXXXXXXXXXXXX"
    echo "  $0 myorg AXXXXXXXXXXXXXXXXXXXX my-runner-name  # org-level runner"
    exit 1
fi

GITHUB_TARGET="$1"
RUNNER_TOKEN="$2"
if [ -n "$3" ]; then
    RUNNER_NAME="$3"
fi

# Determine if org-level or repo-level
case "$GITHUB_TARGET" in
    */*)
        RUNNER_URL="https://github.com/${GITHUB_TARGET}"
        ;;
    *)
        RUNNER_URL="https://github.com/${GITHUB_TARGET}"
        ;;
esac

echo "=== GitHub Actions Runner Setup for FreeBSD ==="
echo "Target:  ${GITHUB_TARGET}"
echo "Runner:  ${RUNNER_NAME}"
echo "Labels:  ${RUNNER_LABELS}"
echo ""

# --- Step 1: Install required packages ---
echo ">>> Installing packages..."
pkg install -y \
    git \
    node \
    npm \
    python3 \
    py311-pip \
    curl \
    ca_root_nss \
    bash \
    jq \
    go  # needed only if building from source

# --- Step 2: Create runner user ---
if ! pw usershow "${RUNNER_USER}" > /dev/null 2>&1; then
    echo ">>> Creating user '${RUNNER_USER}'..."
    pw useradd -n "${RUNNER_USER}" -m -s /usr/local/bin/bash -c "GitHub Actions Runner" -L default
else
    echo ">>> User '${RUNNER_USER}' already exists."
fi
cap_mkdb /etc/login.conf

# --- Step 3: Download github-act-runner ---
echo ">>> Downloading github-act-runner v${RUNNER_VERSION}..."
ARCH=$(uname -m)
case "${ARCH}" in
    amd64|x86_64)  ARCH_SUFFIX="amd64" ;;
    aarch64|arm64) ARCH_SUFFIX="arm64" ;;
    *)
        echo "ERROR: Unsupported architecture: ${ARCH}"
        exit 1
        ;;
esac

mkdir -p "${RUNNER_DIR}"
cd "${RUNNER_DIR}"

DOWNLOAD_URL="https://github.com/ChristopherHX/github-act-runner/releases/download/v${RUNNER_VERSION}/binary-freebsd-${ARCH_SUFFIX}.tar.gz"
echo ">>> Fetching: ${DOWNLOAD_URL}"
curl -fsSL "${DOWNLOAD_URL}" -o runner.tar.gz
tar xzf runner.tar.gz
rm runner.tar.gz
chmod +x github-act-runner

chown -R "${RUNNER_USER}:${RUNNER_USER}" "${RUNNER_DIR}"

# --- Step 4: Configure the runner ---
echo ">>> Configuring runner..."
su -l "${RUNNER_USER}" -c "
    cd '${RUNNER_DIR}' && \
    ./github-act-runner configure \
        --url '${RUNNER_URL}' \
        --token '${RUNNER_TOKEN}' \
        --name '${RUNNER_NAME}' \
        --labels '${RUNNER_LABELS}' \
        --unattended
"

# --- Step 5: Create rc.d service ---
echo ">>> Creating rc.d service..."
cat > /usr/local/etc/rc.d/github_act_runner << 'RCEOF'
#!/bin/sh

# PROVIDE: github_act_runner
# REQUIRE: NETWORKING DAEMON
# KEYWORD: shutdown

. /etc/rc.subr

name="github_act_runner"
rcvar="${name}_enable"

: ${github_act_runner_enable:="NO"}
: ${github_act_runner_user:="github-runner"}
: ${github_act_runner_dir:="/home/github-runner/actions-runner"}
: ${github_act_runner_log:="/var/log/github-act-runner.log"}

pidfile="/var/run/${name}.pid"

start_cmd="${name}_start"
stop_cmd="${name}_stop"
status_cmd="${name}_status"

github_act_runner_start()
{
    install -o "${github_act_runner_user}" -m 644 /dev/null "${pidfile}"
    install -o "${github_act_runner_user}" -m 644 /dev/null "${github_act_runner_log}"
    /usr/sbin/daemon -f -p "${pidfile}" -o "${github_act_runner_log}" \
        su -l "${github_act_runner_user}" -c "cd ${github_act_runner_dir} && ./github-act-runner run"
    echo "Started ${name}."
}

github_act_runner_stop()
{
    if [ -f "${pidfile}" ]; then
        kill $(cat "${pidfile}") 2>/dev/null && echo "Stopped ${name}."
        rm -f "${pidfile}"
    else
        echo "${name} is not running."
    fi
}

github_act_runner_status()
{
    if [ -f "${pidfile}" ] && kill -0 $(cat "${pidfile}") 2>/dev/null; then
        echo "${name} is running as pid $(cat "${pidfile}")."
    else
        echo "${name} is not running."
    fi
}

load_rc_config $name
run_rc_command "$1"
RCEOF
chmod +x /usr/local/etc/rc.d/github_act_runner

# --- Step 6: Enable and start the service ---
echo ">>> Enabling service..."
sysrc github_act_runner_enable="YES"

echo ">>> Starting runner..."
service github_act_runner start

echo ""
echo "=== Setup complete ==="
echo ""
echo "Runner '${RUNNER_NAME}' is registered and running."
echo ""
echo "Useful commands:"
echo "  service github_act_runner status   # Check status"
echo "  service github_act_runner stop     # Stop runner"
echo "  service github_act_runner restart  # Restart runner"
echo "  tail -f /var/log/github-act-runner.log  # View logs"
echo ""
echo "To update the runner later:"
echo "  service github_act_runner stop"
echo "  cd ${RUNNER_DIR}"
echo "  # Download new version and extract"
echo "  service github_act_runner start"
echo ""
echo "Limitations:"
echo "  - No Docker support (FreeBSD uses jails instead)"
echo "  - Many marketplace actions assume Linux — use FreeBSD-native commands"
echo "  - Node.js actions require 'node' in PATH (installed above)"
echo "  - Update the runner binary manually when new versions release"
