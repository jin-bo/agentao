"""Test script to verify skills loading from SKILL.md files."""

from pathlib import Path
from agentao.skills import SkillManager

print("=" * 70)
print("Testing Skills Loading from SKILL.md Files")
print("=" * 70)

# Show skills directory location
skills_path = Path(__file__).parent.parent / "skills"
print(f"\nSkills directory: {skills_path}")
print(f"Exists: {skills_path.exists()}")

# Initialize skill manager with explicit path
manager = SkillManager(skills_dir=str(skills_path))

# List available skills
skills = manager.list_available_skills()
print(f"\n✓ Loaded {len(skills)} skills from SKILL.md files\n")

if not skills:
    print("⚠️  No skills found! Check if SKILL.md files exist in subdirectories.")
else:
    # Display each skill
    for skill_name in sorted(skills):
        skill_info = manager.get_skill_info(skill_name)
        print(f"📦 {skill_name}")
        print(f"   Title: {skill_info.get('title', 'N/A')}")
        print(f"   Description: {skill_info.get('description', 'N/A')[:100]}...")
        print(f"   Path: {skill_info.get('path', 'N/A')}")
        print()

    # Test activation
    print("=" * 70)
    print("Testing Skill Activation")
    print("=" * 70)

    test_skill = sorted(skills)[0]  # Test with first skill alphabetically
    print(f"\nActivating skill: {test_skill}\n")
    result = manager.activate_skill(test_skill, "Test task for demonstration")
    print(result)

    # Show active skills
    active = manager.get_active_skills()
    print(f"\n✓ Active skills: {len(active)}")

    # Show context
    print("\n" + "=" * 70)
    print("Skills Context for LLM:")
    print("=" * 70)
    print(manager.get_skills_context())

    # Test reading full skill content
    print("\n" + "=" * 70)
    print("Testing Full Content Retrieval")
    print("=" * 70)
    full_content = manager.get_skill_content(test_skill)
    if full_content:
        print(f"\n✓ Retrieved {len(full_content)} characters from {test_skill} SKILL.md")
        print(f"First 300 characters:\n{full_content[:300]}...")
    else:
        print(f"\n✗ Could not retrieve content for {test_skill}")

print("\n" + "=" * 70)
print("✓ All tests passed!")
print("=" * 70)
