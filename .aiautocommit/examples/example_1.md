# Example 1

```
diff --git c/.tmux-shell.sh i/.tmux-shell.sh
index a34433f..01d2e9f 100755
--- c/.tmux-shell.sh
+++ i/.tmux-shell.sh
@@ -14,8 +14,8 @@ while [[ $counter -lt 20 ]]; do
   session="${session_uid}-${counter}"
 
   # if the session doesn't exist, create it
-  if ! tmux has-session -t "$session" 2>/dev/null; then
-    tmux new -ADs "$session"
+  if ! /opt/homebrew/bin/tmux has-session -t "$session" 2>/dev/null; then
+    /opt/homebrew/bin/tmux new -ADs "$session"
     break
   fi
```

This diff is short and should have no extended commit message.

Example commit message:

fix: use full path to tmux in .tmux-shell.sh