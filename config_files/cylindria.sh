#!/bin/bash

# User can configure startup by removing the reference in /etc.portal.yaml - So wait for that file and check it
while [ ! -f "$(realpath -q /etc/portal.yaml 2>/dev/null)" ]; do
    echo "Waiting for /etc/portal.yaml before starting ${PROC_NAME}..." | tee -a "/var/log/portal/${PROC_NAME}.log"
    sleep 1
done

## Check for comfyui in the portal config
#search_term="comfyui"
#search_pattern=$(echo "$search_term" | sed 's/[ _-]/[ _-]/g')
#if ! grep -qiE "^[^#].*${search_pattern}" /etc/portal.yaml; then
#    echo "Skipping startup for ${PROC_NAME} (not in /etc/portal.yaml)" | tee -a "/var/log/portal/${PROC_NAME}.log"
#    exit 0
#fi

# Activate the venv
. /venv/main/bin/activate

# Wait for provisioning to complete

#while [ -f "/.provisioning" ]; do
#    echo "$PROC_NAME startup paused until instance provisioning has completed (/.provisioning present)"
#    sleep 10
#done

# Avoid git errors because we run as root but files are owned by 'user'
export GIT_CONFIG_GLOBAL=/tmp/temporary-git-config
git config --file $GIT_CONFIG_GLOBAL --add safe.directory '*'

# count available CUDA devices
CYLINDRIA_NUM_GPUS=$(nvidia-smi -L | wc -l)

export COMFYUI_BASE_URL="http://127.0.0.1:18188"
# Set CYLINDRIA_NUM_GPUS before launching (defaults to 1 if unset)
export CYLINDRIA_NUM_GPUS="${CYLINDRIA_NUM_GPUS:-1}"

echo "Launching Cylindria with $CYLINDRIA_NUM_GPUS GPUs"

# Launch Cylindria
cd /workspace/Cylindria
python3 -m cylindria --port 8100 --numberOfGpus "${CYLINDRIA_NUM_GPUS}"
