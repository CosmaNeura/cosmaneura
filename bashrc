export PATH="$HOME/.local/bin/mise:$PATH"

eval "$(mise activate bash)"
eval "$(direnv hook bash)"

direnv allow .
