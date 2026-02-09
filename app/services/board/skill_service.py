"""Skill service for BrinBoard - reads from SKILLS_DIR"""
import os
from pathlib import Path
from typing import Dict, List, Optional
from fastapi import HTTPException

from app import config


def list_skills() -> List[Dict]:
    """List available skills from SKILLS_DIR"""
    skills_dir = Path(os.getenv('SKILLS_DIR', os.path.expanduser('~/clawd/skills')))
    
    if not skills_dir.exists():
        return []
    
    skills = []
    
    for item in skills_dir.iterdir():
        if not item.is_dir():
            continue
        
        skill_md = item / "SKILL.md"
        if not skill_md.exists():
            continue
        
        # Read skill name and description from SKILL.md
        try:
            content = skill_md.read_text()
            lines = content.split('\n')
            
            name = item.name
            description = ""
            
            # Try to extract from first heading or first paragraph
            for line in lines:
                if line.startswith('# '):
                    name = line[2:].strip()
                elif line.strip() and not line.startswith('#'):
                    description = line.strip()
                    break
            
            skills.append({
                "name": item.name,
                "display_name": name,
                "description": description,
                "path": str(item)
            })
        except Exception:
            # If we can't read the file, just use directory name
            skills.append({
                "name": item.name,
                "display_name": item.name,
                "description": "",
                "path": str(item)
            })
    
    return sorted(skills, key=lambda x: x['name'])


def get_skill(name: str) -> Optional[Dict]:
    """Get skill details by name"""
    skills_dir = Path(os.getenv('SKILLS_DIR', os.path.expanduser('~/clawd/skills')))
    skill_path = skills_dir / name
    
    if not skill_path.exists() or not skill_path.is_dir():
        raise HTTPException(status_code=404, detail="Skill not found")
    
    skill_md = skill_path / "SKILL.md"
    if not skill_md.exists():
        raise HTTPException(status_code=404, detail="Skill documentation not found")
    
    content = skill_md.read_text()
    lines = content.split('\n')
    
    display_name = name
    description = ""
    
    # Extract name and description
    for line in lines:
        if line.startswith('# '):
            display_name = line[2:].strip()
        elif line.strip() and not line.startswith('#'):
            description = line.strip()
            break
    
    return {
        "name": name,
        "display_name": display_name,
        "description": description,
        "path": str(skill_path),
        "content": content
    }
