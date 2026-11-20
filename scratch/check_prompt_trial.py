import sys
import os
sys.path.insert(0, os.path.abspath("."))
from dotenv import load_dotenv

load_dotenv()

from src.core import prompt as prompt_module

sample_state = {"course": "kathak", "stage": "NEW", "language": "Hinglish"}
rendered = prompt_module.format_system_prompt(sample_state, "kathak")

# Find occurrences of 'trial' in rendered prompt
lines = rendered.splitlines()
print(f"Total lines in rendered prompt: {len(lines)}")
for i, line in enumerate(lines):
    if "trial" in line.lower():
        print(f"L{i}: {line}")
