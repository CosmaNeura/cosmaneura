```
diff --git a/.git-functions b/.git-functions
index ba4be51..2bbb33b 100644
--- a/.git-functions
+++ b/.git-functions
@@ -108,7 +108,7 @@ github_reopen_pr() {
   gh pr create --web --repo "$REPO" --title "$TITLE" --body "$BODY" --head "$HEAD_REPO_OWNER:$HEAD_REF" --base "$BASE_BRANCH"
 }
 
-# add a license to an existing project/repo
+# add a license to an existing project/repo, both as a license file and license metadata
 add_mit_license() {
   # Check if the current folder is tied to a GitHub repository
   if ! gh repo view >/dev/null 2>&1; then
@@ -148,7 +148,8 @@ add_mit_license() {
   echo "MIT License added to the repository."
 }
 
-# render readme content on clipboard and replace username and password
+# render readme content on clipboard and replace username and repo
+# useful for custom templates I have in my notes
 render-git-template() {
   local GH_USERNAME=$(gh repo view --json owner --jq '.owner.login' | tr -d '[:space:]')
   local GH_REPO=$(gh repo view --json name --jq '.name' | tr -d '[:space:]')
@@ -158,3 +159,22 @@ render-git-template() {
   TEMPLATE=${TEMPLATE//REPO/$GH_REPO}
   echo $TEMPLATE | tr -ds '\n' ' '
 }
+
+# extracts all file(s) in a git repo path into PWD. Helpful for lifting source from an existing open source project.
+# usage: git-extract https://github.com/vantezzen/shadcn-registry-template/blob/main/scripts/
+git-extract() {
+  local url=$1
+  # Extract owner/repo/branch/path from GitHub URL
+  local parts=(${(s:/:)${url/https:\/\/github.com\//}})
+  local owner=$parts[1]
+  local repo=$parts[2] 
+  local branch=$parts[4]
+  local filepath=${(j:/:)parts[5,-1]}
+  
+  # Build tarball URL and folder name
+  local tarball="https://github.com/$owner/$repo/archive/refs/heads/$branch.tar.gz"
+  local foldername="$repo-$branch"
+  
+  # Extract just the specified path
+  curl -L $tarball | tar xz --strip=1 "$foldername/$filepath"
+}
\ No newline at end of file
```

This diff is short and should have no extended commit message. The updated comments of `render-git-template` and 
`update-mit-license` should be ignored when writing the commit message.

Example commit message:

feat: git-extract function to download a folder or file from a git repo