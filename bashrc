export PATH="$HOME/.local/bin:$PATH"

eval "$(mise activate bash)"
mise install
eval "$(direnv hook bash)"

direnv allow .
