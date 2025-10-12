import re

with open('app.py', 'r') as f:
    content = f.read()

checks = {
    "CORS before routes": "add_middleware" in content and content.index("add_middleware") < content.index("@app.get"),
    "/assets mount": '"/assets"' in content and "frontend/build/assets" in content,
    "Catch-all route": '"/{full_path:path}"' in content,
    "FileResponse import": "from fastapi.responses import FileResponse" in content,
}

print("App.py Configuration Check:")
for check, passed in checks.items():
    print(f"  {'✅' if passed else '❌'} {check}")

if all(checks.values()):
    print("\n🎉 All checks passed!")
else:
    print("\n⚠️  Some checks failed - review the changes above")