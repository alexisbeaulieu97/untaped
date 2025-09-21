# untaped-github

GitHub API support for the **untaped** Infrastructure-as-Code toolkit. This package enables you to manage GitHub repository operations through declarative YAML configurations, similar to how untaped manages Ansible Tower resources.

## Overview

untaped-github allows you to:
- 📁 Read files from GitHub repositories
- 📂 List directory contents in GitHub repositories
- ⚙️ Use Jinja2 templates for dynamic configurations
- ✅ Validate all operations before execution
- 🎨 Beautiful CLI output with rich formatting
- 🔄 Dry-run mode for safe testing

## Installation

### Prerequisites

1. **GitHub CLI**: Install the official GitHub CLI tool
   ```bash
   # macOS
   brew install gh

   # Ubuntu/Debian
   sudo apt install gh

   # Windows
   winget install --id GitHub.cli
   ```

2. **Authentication**: Authenticate with GitHub
   ```bash
   gh auth login
   ```

3. **untaped**: Install the untaped toolkit
   ```bash
   # Install from source
   uv sync

   # Or install as a tool
   uv tool install --from packages/untaped-cli
   ```

## Quick Start

### 1. Create a Configuration File

Create `untaped-github.yaml`:

```yaml
repository: "octocat/Hello-World"
file_path: "README.md"
ref: "main"
```

### 2. Read a File

```bash
untaped github read-file --config-file untaped-github.yaml
```

### 3. List Directory Contents

Create `list-config.yaml`:

```yaml
repository: "octocat/Hello-World"
directory_path: "docs"
recursive: true
```

```bash
untaped github list-directory --config-file list-config.yaml
```

## Configuration

### Basic Configuration

```yaml
# untaped-github.yaml
repository: "owner/repo"          # GitHub repository in owner/repo format
file_path: "path/to/file.md"      # Path to file within repository
ref: "main"                       # Branch, tag, or commit SHA (optional, defaults to main)
```

### Directory Listing Configuration

```yaml
# list-config.yaml
repository: "owner/repo"          # GitHub repository in owner/repo format
directory_path: "docs"            # Directory path (use "." for root)
recursive: true                   # List subdirectories recursively (optional)
```

### Template Variables

Use Jinja2 templates with variable files:

```yaml
# config.yaml
repository: "{{ org }}/{{ repo }}"
file_path: "{{ env }}/{{ file_path }}"

# vars.yaml
org: "myorg"
repo: "myproject"
env: "production"
file_path: "README.md"
```

```bash
untaped github read-file --config-file config.yaml --vars-file vars.yaml
```

### Environment Variables

You can use environment variables in templates:

```yaml
# config.yaml
repository: "{{ env.GITHUB_REPO }}"
file_path: "{{ env.GITHUB_FILE_PATH }}"

# Set environment variables
export GITHUB_REPO="octocat/Hello-World"
export GITHUB_FILE_PATH="README.md"
```

## Command Reference

### read-file

Read a file from a GitHub repository.

```bash
untaped github read-file [OPTIONS] CONFIG_FILE

Options:
  --config-file PATH      YAML configuration file path
  --vars-file FILE        Variable file(s) for template rendering
  --dry-run              Show what would be done without executing
  --verbose, -v          Show detailed output
```

**Exit Codes:**
- `0`: Success
- `1`: Validation error
- `2`: Authentication error
- `3`: Permission error
- `4`: Network error
- `5`: File not found
- `6`: Configuration error
- `7`: API error
- `100`: Unknown error

### list-directory

List files in a GitHub repository directory.

```bash
untaped github list-directory [OPTIONS] CONFIG_FILE

Options:
  --config-file PATH      YAML configuration file path
  --vars-file FILE        Variable file(s) for template rendering
  --dry-run              Show what would be done without executing
  --verbose, -v          Show detailed output
  --recursive, -r        List files recursively
```

## Examples

### Read a Configuration File

```bash
# Read a config file from a repository
untaped github read-file --config-file - <<EOF
repository: "kubernetes/kubernetes"
file_path: ".github/workflows/ci.yaml"
ref: "main"
EOF
```

### List All Files in a Directory

```bash
# List all files in the root directory
untaped github list-directory --config-file - <<EOF
repository: "microsoft/vscode"
directory_path: "."
EOF
```

### Use with Templates

```bash
# Create config with template variables
cat > config.yaml <<EOF
repository: "{{ org }}/{{ repo }}"
file_path: "{{ file_path }}"
ref: "{{ branch }}"
EOF

cat > vars.yaml <<EOF
org: "github"
repo: "gitignore"
file_path: "Global/Linux.gitignore"
branch: "main"
EOF

# Execute with variables
untaped github read-file --config-file config.yaml --vars-file vars.yaml
```

### Check Before Executing

```bash
# Dry run to validate configuration and permissions
untaped github read-file --config-file config.yaml --dry-run --verbose
```

## Error Handling

The tool provides comprehensive error handling with helpful messages:

- **Authentication Errors**: Clear instructions to run `gh auth login`
- **Permission Errors**: Information about repository access requirements
- **Network Errors**: Guidance for troubleshooting connectivity issues
- **File Not Found**: Specific details about missing files
- **Configuration Errors**: Detailed validation error messages

## Development

### Project Structure

```
packages/untaped-github/
├── src/untaped_github/
│   ├── models/          # Pydantic models for validation
│   ├── api/             # GitHub CLI wrapper and error handling
│   ├── services/        # Business logic and configuration processing
│   ├── validators/      # Configuration validation
│   └── logging.py       # Structured logging setup
└── tests/               # Comprehensive test suite
```

### Testing

```bash
# Run all tests
uv run --package untaped-github pytest

# Run specific test types
uv run --package untaped-github pytest tests/contract/    # Contract tests
uv run --package untaped-github pytest tests/integration/ # Integration tests
uv run --package untaped-github pytest tests/unit/        # Unit tests
uv run --package untaped-github pytest tests/performance/ # Performance tests

# Run with coverage
uv run --package untaped-github pytest --cov=src --cov-report=html
```

### Code Quality

```bash
# Format code
uv run --package untaped-github ruff format .

# Check linting
uv run --package untaped-github ruff check .

# Type checking
uv run --package untaped-github mypy src/
```

## Contributing

1. Follow the existing code patterns and architecture
2. Write tests first (TDD approach)
3. Ensure all tests pass
4. Maintain backward compatibility
5. Follow the untaped constitution principles

## License

This package follows the same license as the main untaped project.

## Support

- GitHub Issues: Report bugs and request features
- Documentation: See the main untaped documentation
- Community: Join the untaped community discussions
