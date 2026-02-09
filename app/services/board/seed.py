"""Seed script for BrinBoard - creates sample data (idempotent)"""
import sys
import os

# Add project root to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../../..')))

from app.services.database import get_database
from app.services.board import project_service, task_service, hook_service, tag_service


def seed():
    """Create sample BrinBoard data"""
    db = get_database()
    
    print("🌱 Seeding BrinBoard data...")
    
    # Get first user (assuming at least one user exists)
    user_row = db.fetchone("SELECT id FROM users LIMIT 1")
    if not user_row:
        print("❌ No users found. Create a user first via /api/auth/register")
        return
    
    user_id = user_row['id']
    
    # Create tags (idempotent)
    print("\n📌 Creating tags...")
    tags = {}
    tag_specs = [
        ("Marketing", "#f59e0b"),
        ("Dev", "#3b82f6"),
        ("Analysis", "#8b5cf6"),
        ("Maintenance", "#6b7280")
    ]
    
    for tag_name, color in tag_specs:
        existing = db.fetchone("SELECT id FROM bb_tags WHERE name = ?", (tag_name,))
        if existing:
            tags[tag_name] = existing['id']
            print(f"  ✓ Tag '{tag_name}' already exists")
        else:
            tag = tag_service.create_tag(tag_name, color)
            tags[tag_name] = tag['id']
            print(f"  + Created tag '{tag_name}'")
    
    # Create projects (idempotent)
    print("\n📁 Creating projects...")
    projects = {}
    project_specs = [
        {
            "name": "Content Pipeline",
            "description": "Automated content generation and SEO optimization",
            "prompt": "You are a content creation agent. Generate blog posts, optimize for SEO, and maintain the content calendar.",
            "settings": {
                "priority": "high",
                "skills": ["copywriting", "seo-optimization"]
            }
        },
        {
            "name": "Data Analysis",
            "description": "Weekly data analysis and reporting",
            "prompt": "Analyze CSV data, generate insights, and create visualizations.",
            "settings": {
                "priority": "medium",
                "skills": ["python-execution"]
            }
        }
    ]
    
    for spec in project_specs:
        existing = db.fetchone("SELECT id FROM bb_projects WHERE name = ?", (spec['name'],))
        if existing:
            projects[spec['name']] = existing['id']
            print(f"  ✓ Project '{spec['name']}' already exists")
        else:
            project = project_service.create_project(
                owner_id=user_id,
                name=spec['name'],
                description=spec['description'],
                prompt=spec['prompt'],
                settings=spec['settings']
            )
            projects[spec['name']] = project['id']
            print(f"  + Created project '{spec['name']}'")
    
    # Create tasks (idempotent)
    print("\n📋 Creating tasks...")
    tasks = {}
    task_specs = [
        {
            "title": "Write Q1 blog posts",
            "description": "Create 3 blog posts for Q1 marketing campaign",
            "project": "Content Pipeline",
            "status": "idle",
            "priority": "high",
            "tags": ["Marketing"]
        },
        {
            "title": "Optimize existing content",
            "description": "Review and optimize top 20 pages for SEO",
            "project": "Content Pipeline",
            "status": "active",
            "priority": "medium",
            "tags": ["Marketing", "Dev"]
        },
        {
            "title": "Analyze sales data",
            "description": "Generate monthly sales report",
            "project": "Data Analysis",
            "status": "idle",
            "priority": "high",
            "tags": ["Analysis"]
        },
        {
            "title": "Customer feedback analysis",
            "description": "Analyze customer survey results",
            "project": "Data Analysis",
            "status": "active",
            "priority": "medium",
            "tags": ["Analysis"]
        },
        {
            "title": "Update API documentation",
            "description": "Sync API docs with latest changes",
            "project": None,
            "status": "user_input_needed",
            "priority": "low",
            "tags": ["Dev", "Maintenance"]
        },
        {
            "title": "Archive old reports",
            "description": "Clean up and archive reports older than 1 year",
            "project": None,
            "status": "finished",
            "priority": "low",
            "tags": ["Maintenance"]
        }
    ]
    
    for spec in task_specs:
        project_id = projects.get(spec['project']) if spec['project'] else None
        
        # Check if task exists (by title + project)
        if project_id:
            existing = db.fetchone(
                "SELECT id FROM bb_tasks WHERE title = ? AND project_id = ?",
                (spec['title'], project_id)
            )
        else:
            existing = db.fetchone(
                "SELECT id FROM bb_tasks WHERE title = ? AND project_id IS NULL",
                (spec['title'],)
            )
        
        if existing:
            tasks[spec['title']] = existing['id']
            print(f"  ✓ Task '{spec['title']}' already exists")
        else:
            task = task_service.create_task(
                title=spec['title'],
                description=spec['description'],
                project_id=project_id,
                status=spec['status'],
                priority=spec['priority']
            )
            tasks[spec['title']] = task['id']
            
            # Add tags
            for tag_name in spec.get('tags', []):
                if tag_name in tags:
                    task_service.add_tag_to_task(task['id'], tags[tag_name])
            
            print(f"  + Created task '{spec['title']}'")
    
    # Create subtasks for first task (idempotent)
    print("\n✅ Creating subtasks...")
    parent_task_id = tasks.get("Write Q1 blog posts")
    if parent_task_id:
        subtask_titles = [
            "Research trending topics",
            "Draft outline for post #1",
            "Write and publish post #1"
        ]
        
        for subtask_title in subtask_titles:
            existing = db.fetchone(
                "SELECT id FROM bb_tasks WHERE title = ? AND parent_id = ?",
                (subtask_title, parent_task_id)
            )
            if existing:
                print(f"  ✓ Subtask '{subtask_title}' already exists")
            else:
                task_service.create_subtask(parent_task_id, subtask_title)
                print(f"  + Created subtask '{subtask_title}'")
    
    # Create hooks (idempotent)
    print("\n🪝 Creating hooks...")
    hook_specs = [
        {
            "name": "Session Start Logger",
            "project": "Content Pipeline",
            "event": "SessionStart",
            "action_type": "log_metadata",
            "action_data": {"fields": ["timestamp", "agent_id", "task_id"]},
            "description": "Log session start metadata"
        },
        {
            "name": "Content Quality Check",
            "project": "Content Pipeline",
            "event": "PostToolUse",
            "action_type": "run_prompt",
            "action_data": {"prompt": "Review the generated content for quality and SEO best practices"},
            "description": "Quality check after content generation"
        },
        {
            "name": "Analysis Summary",
            "project": "Data Analysis",
            "event": "Stop",
            "action_type": "run_prompt",
            "action_data": {"prompt": "Summarize key findings from this analysis"},
            "description": "Generate summary on completion"
        },
        {
            "name": "Webhook Notification",
            "project": "Data Analysis",
            "event": "SessionEnd",
            "action_type": "webhook",
            "action_data": {"url": "http://localhost:8081/api/board/stats", "method": "GET"},
            "description": "Notify on session end"
        }
    ]
    
    for spec in hook_specs:
        project_id = projects.get(spec['project'])
        
        # Check if hook exists (by name + project)
        existing = db.fetchone(
            "SELECT id FROM bb_hooks WHERE name = ? AND project_id = ?",
            (spec['name'], project_id)
        )
        
        if existing:
            print(f"  ✓ Hook '{spec['name']}' already exists")
        else:
            hook_service.create_hook(
                name=spec['name'],
                project_id=project_id,
                event=spec['event'],
                action_type=spec['action_type'],
                action_data=spec['action_data'],
                description=spec.get('description')
            )
            print(f"  + Created hook '{spec['name']}'")
    
    # Add comments to tasks
    print("\n💬 Creating comments...")
    comment_specs = [
        {
            "task": "Write Q1 blog posts",
            "content": "Focus on trending topics in AI and automation"
        },
        {
            "task": "Analyze sales data",
            "content": "Compare Q4 performance vs previous year"
        }
    ]
    
    for spec in comment_specs:
        task_id = tasks.get(spec['task'])
        if task_id:
            # Check if similar comment exists
            existing = db.fetchone(
                "SELECT id FROM bb_comments WHERE task_id = ? AND content = ?",
                (task_id, spec['content'])
            )
            if existing:
                print(f"  ✓ Comment on '{spec['task']}' already exists")
            else:
                task_service.add_comment(task_id, spec['content'], user_id=user_id)
                print(f"  + Added comment to '{spec['task']}'")
    
    print("\n✅ Seeding complete!")


if __name__ == "__main__":
    try:
        seed()
    except Exception as e:
        print(f"\n❌ Error during seeding: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
