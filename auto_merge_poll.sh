#!/bin/bash
# Auto-poll for new branches and merge them

cd /home/tech/PeanutChat

while true; do
    echo "[$(date)] Checking for new branches..."
    
    # Fetch all remotes
    git fetch --all 2>/dev/null
    
    # Get list of unmerged claude/* branches
    for branch in $(git branch -r | grep 'origin/claude/' | sed 's/origin\///'); do
        # Check if branch has commits not in main
        commits=$(git log HEAD..origin/$branch --oneline 2>/dev/null | wc -l)
        
        if [ "$commits" -gt 0 ]; then
            echo "[$(date)] Found $commits new commit(s) on $branch, attempting merge..."
            
            # Try to merge
            if git merge origin/$branch -m "Auto-merge $branch" 2>/dev/null; then
                echo "[$(date)] Merged $branch successfully"
                
                # Restart peanutchat
                echo "[$(date)] Restarting peanutchat..."
                sudo systemctl restart peanutchat
                
                echo "[$(date)] Done!"
            else
                echo "[$(date)] Merge conflict on $branch, skipping (manual resolution needed)"
                git merge --abort 2>/dev/null
            fi
        fi
    done
    
    echo "[$(date)] Sleeping for 5 minutes..."
    sleep 300
done
