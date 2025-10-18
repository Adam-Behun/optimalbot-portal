import os

def print_directory_tree(path='.', level=0):
    ignore = {
        '.github', 'fine-tune', '.git', '__pycache__', 'static', 'venv', '.next', 'node_modules',
        'package-lock.json', '.gitignore', 'notes.txt', 'README.md', 'tasks.md', 'Notes.md', 'print_tree.py'
    }  # Ignore these files/folders
    items = sorted(os.listdir(path))
    for i, item in enumerate(items):
        if item in ignore:
            continue
        prefix = '├── ' if i < len(items) - 1 else '└── '
        print('    ' * level + prefix + item)
        full_path = os.path.join(path, item)
        if os.path.isdir(full_path):
            print_directory_tree(full_path, level + 1)

print_directory_tree()