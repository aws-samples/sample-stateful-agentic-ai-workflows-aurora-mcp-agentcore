# Verification Script for Meridian Dependencies

import sys

print(f"Python: {sys.version}\n")
print("Verifying installed packages...\n")

# Test all imports with proper error handling
packages_status = []

# Strands
try:
    import strands  # noqa: F401  (import is the availability probe)
    packages_status.append(("✅ Strands", "installed"))
except ImportError:
    packages_status.append(("❌ Strands", "NOT INSTALLED"))

# Boto3
try:
    import boto3
    packages_status.append(("✅ Boto3", boto3.__version__))
except ImportError:
    packages_status.append(("❌ Boto3", "NOT INSTALLED"))

# numpy
try:
    import numpy
    packages_status.append(("✅ numpy", numpy.__version__))
except ImportError:
    packages_status.append(("❌ numpy", "NOT INSTALLED"))

# rich
try:
    from rich.console import Console  # noqa: F401  (availability probe)
    packages_status.append(("✅ rich", "installed"))
except ImportError:
    packages_status.append(("❌ rich", "NOT INSTALLED"))

# python-dotenv
try:
    from dotenv import load_dotenv  # noqa: F401  (availability probe)
    packages_status.append(("✅ python-dotenv", "installed"))
except ImportError:
    packages_status.append(("❌ python-dotenv", "NOT INSTALLED"))

# pydantic
try:
    import pydantic
    packages_status.append(("✅ pydantic", pydantic.__version__))
except ImportError:
    packages_status.append(("❌ pydantic", "NOT INSTALLED"))

# FastAPI
try:
    import fastapi
    packages_status.append(("✅ FastAPI", fastapi.__version__))
except ImportError:
    packages_status.append(("❌ FastAPI", "NOT INSTALLED"))

# Hypothesis (for property-based testing)
try:
    import hypothesis
    packages_status.append(("✅ Hypothesis", hypothesis.__version__))
except ImportError:
    packages_status.append(("❌ Hypothesis", "NOT INSTALLED"))

# Print results
print("=" * 70)
for package, version in packages_status:
    print(f"{package:.<50} {version}")
print("=" * 70)

# Check if all critical packages are installed
failed = [status for status in packages_status if "❌" in status[0]]

if not failed:
    print("\n🎉 All dependencies installed successfully!")
    print("\n✅ You're ready to proceed with:")
    print("   1. Aurora connection test (RDS Data API)")
    print("   2. Database schema initialization")
    print("   3. Running demos")
else:
    print("\n⚠️  Some dependencies are missing!")
    print("\nMissing packages:")
    for package, _error in failed:
        print(f"  • {package.replace('❌ ', '')}")
    print("\n💡 Run: pip install -r requirements.txt")

print("\n" + "=" * 70)
