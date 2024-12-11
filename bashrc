export PATH="$HOME/.local/bin:$PATH"

eval "$(mise activate bash)"
eval "$(direnv hook bash)"

direnv allow .
