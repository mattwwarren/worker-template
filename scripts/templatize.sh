#!/usr/bin/env bash
# templatize.sh - Convert runnable worker_template to copier template
#
# This script transforms the working Python project back into a Copier template
# by replacing hardcoded "worker_template" references with Jinja2 template
# variables ({{ project_slug }}).
#
# Usage:
#   ./scripts/templatize.sh [output_dir]
#
# Arguments:
#   output_dir - Target directory for templatized output (default: .templatized)
#
# The script:
# 1. Copies the project excluding dev artifacts (.git, __pycache__, .venv, etc.)
# 2. Renames worker_template/ directory to {{ project_slug }}/
# 3. Replaces "worker_template" with "{{ project_slug }}" in Python files
# 4. Updates pyproject.toml, alembic.ini, alembic/env.py
# 5. Preserves existing Jinja2 templated files (QUICKSTART.md, dotenv.example)

set -euo pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Script directory (resolve symlinks)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# Default output directory
OUTPUT_DIR="${1:-.templatized}"

# Convert to absolute path if relative
if [[ ! "${OUTPUT_DIR}" = /* ]]; then
    OUTPUT_DIR="${PROJECT_ROOT}/${OUTPUT_DIR}"
fi

echo -e "${GREEN}=== Templatization Script ===${NC}"
echo "Source: ${PROJECT_ROOT}"
echo "Output: ${OUTPUT_DIR}"
echo ""

# Clean output directory if it exists
if [[ -d "${OUTPUT_DIR}" ]]; then
    echo -e "${YELLOW}Removing existing output directory...${NC}"
    rm -rf "${OUTPUT_DIR}"
fi

# Create output directory
mkdir -p "${OUTPUT_DIR}"

# Step 1: Copy project excluding dev artifacts
echo -e "${GREEN}[1/6] Copying project (excluding dev artifacts)...${NC}"

# Files and directories to exclude
EXCLUDE_PATTERNS=(
    ".git"
    ".venv"
    ".python"
    "__pycache__"
    "*.pyc"
    "*.pyo"
    "*.pyd"
    ".pytest_cache"
    ".mypy_cache"
    ".ruff_cache"
    ".coverage"
    "coverage.json"
    "htmlcov"
    ".env"
    ".env.*"
    "*.egg-info"
    "build"
    "dist"
    ".build"
    ".dist"
    ".DS_Store"
    ".devspace"
    "uploads"
    "docs/_build"
    ".templatized"
    "uv.lock"
    # Template infrastructure files (not for generated projects)
    ".github/workflows/publish-template.yml"
    ".github/workflows/validate-template.yml"
    "scripts/templatize.sh"
)

# Build rsync exclude arguments
RSYNC_EXCLUDES=()
for pattern in "${EXCLUDE_PATTERNS[@]}"; do
    RSYNC_EXCLUDES+=("--exclude=${pattern}")
done

# Copy using rsync (preserves symlinks, permissions)
rsync -a "${RSYNC_EXCLUDES[@]}" "${PROJECT_ROOT}/" "${OUTPUT_DIR}/"

echo "  Copied $(find "${OUTPUT_DIR}" -type f | wc -l) files"

# Template variable - the target directory name with Jinja2 syntax
# Use hex codes in sed pattern to avoid brace interpretation issues
TEMPLATE_VAR='{{ project_slug }}'
SED_REPLACEMENT='\x7B\x7B project_slug \x7D\x7D'

# Step 2: Replace worker_template references in Python files FIRST (before rename)
# This avoids dealing with curly braces in paths
echo -e "${GREEN}[2/6] Replacing references in Python files...${NC}"

# Find all .py files in worker_template directory
if [[ ! -d "${OUTPUT_DIR}/worker_template" ]]; then
    echo -e "${RED}ERROR: worker_template/ directory not found${NC}"
    exit 1
fi

PY_COUNT=0
while IFS= read -r -d '' file; do
    if grep -q "worker_template" "$file" 2>/dev/null; then
        # Replace worker_template with {{ project_slug }}
        sed -i "s/worker_template/${SED_REPLACEMENT}/g" "$file"
        ((PY_COUNT++)) || true  # Prevent exit on first increment (0 returns false)
    fi
done < <(find "${OUTPUT_DIR}/worker_template" -name "*.py" -print0 2>/dev/null)

echo "  Updated ${PY_COUNT} Python files in package"

# Step 3: Rename worker_template/ to {{ project_slug }}/
echo -e "${GREEN}[3/6] Renaming package directory...${NC}"

mv "${OUTPUT_DIR}/worker_template" "${OUTPUT_DIR}/${TEMPLATE_VAR}"
echo "  Renamed: worker_template/ -> ${TEMPLATE_VAR}/"

# Step 4: Update configuration files at project root
echo -e "${GREEN}[4/6] Updating configuration files...${NC}"

# pyproject.toml - replace package references
if [[ -f "${OUTPUT_DIR}/pyproject.toml" ]]; then
    sed -i "s/worker_template/${SED_REPLACEMENT}/g" "${OUTPUT_DIR}/pyproject.toml"
    echo "  Updated: pyproject.toml"
fi

# alembic.ini - no changes needed (doesn't reference worker_template)
echo "  Checked: alembic.ini (no changes needed)"

# alembic/env.py - replace import references
if [[ -f "${OUTPUT_DIR}/alembic/env.py" ]]; then
    sed -i "s/worker_template/${SED_REPLACEMENT}/g" "${OUTPUT_DIR}/alembic/env.py"
    echo "  Updated: alembic/env.py"
fi

# tests/ directory - replace references in test files
if [[ -d "${OUTPUT_DIR}/tests" ]]; then
    TEST_COUNT=0
    while IFS= read -r -d '' file; do
        if grep -q "worker_template" "$file" 2>/dev/null; then
            sed -i "s/worker_template/${SED_REPLACEMENT}/g" "$file"
            ((TEST_COUNT++)) || true
        fi
    done < <(find "${OUTPUT_DIR}/tests" -name "*.py" -print0 2>/dev/null)
    echo "  Updated ${TEST_COUNT} test files"
fi

# _tasks.py - if it references the package
if [[ -f "${OUTPUT_DIR}/_tasks.py" ]]; then
    if grep -q "worker_template" "${OUTPUT_DIR}/_tasks.py" 2>/dev/null; then
        sed -i "s/worker_template/${SED_REPLACEMENT}/g" "${OUTPUT_DIR}/_tasks.py"
        echo "  Updated: _tasks.py"
    fi
fi

# Dockerfile - COPY command references the package directory
if [[ -f "${OUTPUT_DIR}/Dockerfile" ]]; then
    if grep -q "worker_template" "${OUTPUT_DIR}/Dockerfile" 2>/dev/null; then
        sed -i "s/worker_template/${SED_REPLACEMENT}/g" "${OUTPUT_DIR}/Dockerfile"
        echo "  Updated: Dockerfile"
    fi
fi

# devspace.yaml - container and deployment references
if [[ -f "${OUTPUT_DIR}/devspace.yaml" ]]; then
    if grep -q "worker_template" "${OUTPUT_DIR}/devspace.yaml" 2>/dev/null; then
        sed -i "s/worker_template/${SED_REPLACEMENT}/g" "${OUTPUT_DIR}/devspace.yaml"
        echo "  Updated: devspace.yaml"
    fi
fi

# .pre-commit-config.yaml - mypy args reference the package
if [[ -f "${OUTPUT_DIR}/.pre-commit-config.yaml" ]]; then
    if grep -q "worker_template" "${OUTPUT_DIR}/.pre-commit-config.yaml" 2>/dev/null; then
        sed -i "s/worker_template/${SED_REPLACEMENT}/g" "${OUTPUT_DIR}/.pre-commit-config.yaml"
        echo "  Updated: .pre-commit-config.yaml"
    fi
fi

# .github/workflows/ci.yml - references package paths AND contains GitHub Actions expressions
# GitHub Actions uses ${{ }} which conflicts with Jinja2.
# Solution: Wrap ${{ ... }} in {% raw %}...{% endraw %} blocks.
if [[ -f "${OUTPUT_DIR}/.github/workflows/ci.yml" ]]; then
    # First replace package references
    if grep -q "worker_template" "${OUTPUT_DIR}/.github/workflows/ci.yml" 2>/dev/null; then
        sed -i "s/worker_template/${SED_REPLACEMENT}/g" "${OUTPUT_DIR}/.github/workflows/ci.yml"
    fi
    # Wrap GitHub Actions ${{ ... }} expressions in Jinja2 raw blocks
    if grep -qE '\$\{\{' "${OUTPUT_DIR}/.github/workflows/ci.yml" 2>/dev/null; then
        sed -i 's/\${{[^}]*}}/{% raw %}\0{% endraw %}/g' "${OUTPUT_DIR}/.github/workflows/ci.yml"
    fi
    echo "  Updated: .github/workflows/ci.yml (wrapped GHA expressions in raw blocks)"
fi

# Shell scripts in scripts/ - may reference the project name
for script in "${OUTPUT_DIR}"/scripts/*.sh; do
    if [[ -f "$script" ]] && grep -q "worker_template" "$script" 2>/dev/null; then
        sed -i "s/worker_template/${SED_REPLACEMENT}/g" "$script"
        echo "  Updated: scripts/$(basename "$script")"
    fi
done

# Markdown documentation files at project root
MD_COUNT=0
for mdfile in "${OUTPUT_DIR}"/*.md; do
    if [[ -f "$mdfile" ]] && grep -q "worker_template" "$mdfile" 2>/dev/null; then
        sed -i "s/worker_template/${SED_REPLACEMENT}/g" "$mdfile"
        ((MD_COUNT++)) || true
        echo "  Updated: $(basename "$mdfile")"
    fi
done
echo "  Updated ${MD_COUNT} markdown files at root"

# docs/ directory - markdown and rst files
if [[ -d "${OUTPUT_DIR}/docs" ]]; then
    DOCS_COUNT=0
    while IFS= read -r -d '' file; do
        if grep -q "worker_template" "$file" 2>/dev/null; then
            sed -i "s/worker_template/${SED_REPLACEMENT}/g" "$file"
            ((DOCS_COUNT++)) || true
        fi
    done < <(find "${OUTPUT_DIR}/docs" \( -name "*.md" -o -name "*.rst" \) -print0 2>/dev/null)
    echo "  Updated ${DOCS_COUNT} files in docs/"
fi

# .claude/ directory - agent definitions, skills, shared configs
if [[ -d "${OUTPUT_DIR}/.claude" ]]; then
    CLAUDE_COUNT=0
    while IFS= read -r -d '' file; do
        if grep -q "worker_template" "$file" 2>/dev/null; then
            sed -i "s/worker_template/${SED_REPLACEMENT}/g" "$file"
            ((CLAUDE_COUNT++)) || true
        fi
    done < <(find "${OUTPUT_DIR}/.claude" -type f \( -name "*.md" -o -name "*.yaml" -o -name "*.yml" \) -print0 2>/dev/null)
    echo "  Updated ${CLAUDE_COUNT} files in .claude/"
fi

# dotenv.example - environment variable defaults
if [[ -f "${OUTPUT_DIR}/dotenv.example" ]]; then
    if grep -q "worker_template" "${OUTPUT_DIR}/dotenv.example" 2>/dev/null; then
        sed -i "s/worker_template/${SED_REPLACEMENT}/g" "${OUTPUT_DIR}/dotenv.example"
        echo "  Updated: dotenv.example"
    fi
fi

# k8s/ directory - wrap Go template syntax in Jinja2 raw blocks
# Go templates use {{ }} which conflicts with Jinja2
if [[ -d "${OUTPUT_DIR}/k8s" ]]; then
    K8S_COUNT=0
    while IFS= read -r -d '' file; do
        if grep -qE '\{\{.*\}\}' "$file" 2>/dev/null; then
            # Prepend {% raw %} and append {% endraw %}
            # This tells Jinja2 to not process the content as templates
            sed -i '1s/^/{% raw %}\n/' "$file"
            echo '{% endraw %}' >> "$file"
            ((K8S_COUNT++)) || true
        fi
    done < <(find "${OUTPUT_DIR}/k8s" -type f \( -name "*.yaml" -o -name "*.yml" \) -print0 2>/dev/null)
    echo "  Wrapped ${K8S_COUNT} k8s files with Jinja2 raw blocks"
fi

# Step 5: Verify Jinja2 templated files are preserved
echo -e "${GREEN}[5/6] Verifying Jinja2 templated files...${NC}"

JINJA_FILES=(
    "QUICKSTART.md"
    "dotenv.example"
    "copier.yaml"
)

for file in "${JINJA_FILES[@]}"; do
    if [[ -f "${OUTPUT_DIR}/${file}" ]]; then
        # Check if file contains Jinja2 syntax
        if grep -qE '\{\{|\{%' "${OUTPUT_DIR}/${file}" 2>/dev/null; then
            echo "  Preserved: ${file} (contains Jinja2 syntax)"
        else
            echo -e "${YELLOW}  Warning: ${file} may be missing Jinja2 syntax${NC}"
        fi
    else
        echo -e "${YELLOW}  Warning: ${file} not found${NC}"
    fi
done

# Step 6: Verify no remaining hardcoded references
echo -e "${GREEN}[6/6] Checking for remaining worker_template references...${NC}"

# Search for remaining references (excluding expected files)
REMAINING_REFS=$(find "${OUTPUT_DIR}" -type f \
    \( -name "*.py" -o -name "*.yaml" -o -name "*.yml" -o -name "*.md" \
       -o -name "*.toml" -o -name "*.sh" -o -name "*.example" -o -name "*.json" \
       -o -name "*.rst" -o -name "*.txt" -o -name "Dockerfile" \) \
    -not -path "*/.git/*" \
    -not -path "*/__pycache__/*" \
    -exec grep -l "worker_template" {} \; 2>/dev/null || true)

if [[ -n "${REMAINING_REFS}" ]]; then
    echo -e "${RED}ERROR: Found remaining worker_template references in:${NC}"
    echo "${REMAINING_REFS}" | while read -r file; do
        echo "  - ${file}"
        # Show the lines with references
        grep -n "worker_template" "$file" 2>/dev/null | head -3 | sed 's/^/      /'
    done
    echo ""
    echo -e "${YELLOW}These files may need to be added to templatize.sh${NC}"
    exit 1
else
    echo "  No remaining hardcoded references found"
fi

# Summary
echo ""
echo -e "${GREEN}=== Templatization Complete ===${NC}"
echo ""
echo "Output directory: ${OUTPUT_DIR}"
echo ""
echo "Directory structure:"
ls -la "${OUTPUT_DIR}/" | head -20

echo ""
echo "To test the template:"
echo "  copier copy ${OUTPUT_DIR} /tmp/test-project \\"
echo "    --data project_name=\"My Project\" \\"
echo "    --data project_slug=\"my_project\" \\"
echo "    --defaults --trust"
echo ""
echo "To verify the generated project:"
echo "  cd /tmp/test-project"
echo "  uv sync"
echo "  uv run ruff check ."
echo "  uv run mypy ."
echo "  uv run pytest"
