import yaml

VALID_HARNESSES = {"claude_code", "codex", "gemini_cli"}

def load_tier_mappings(path="router.yml"):
    with open(path) as f:
        mappings = yaml.safe_load(f)
    harness = mappings.get("harness")
    if harness not in VALID_HARNESSES:
        raise ValueError(
            f"Unknown or missing harness '{harness}'. "
            f"Set 'harness' in {path} to one of: {', '.join(sorted(VALID_HARNESSES))}"
        )
    return mappings[harness]


print("Loading tier mappings from router.yml...")
print(load_tier_mappings())
