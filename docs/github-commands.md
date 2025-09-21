# GitHub Commands Documentation

This document provides comprehensive documentation for the GitHub CLI commands in the untaped toolkit.

## Overview

The GitHub commands allow you to interact with GitHub repositories through declarative YAML configurations, providing a consistent interface for reading files and listing directory contents.

## Commands

### github read-file

Read a file from a GitHub repository using a YAML configuration.

#### Usage

```bash
untaped github read-file [OPTIONS] CONFIG_FILE

# Examples
untaped github read-file config.yaml
untaped github read-file --config-file config.yaml --dry-run
untaped github read-file -f vars.yaml config.yaml
```

#### Options

| Option | Description | Default |
|--------|-------------|---------|
| `--config-file PATH` | YAML configuration file path | Required |
| `--vars-file FILE` | Variable file(s) for template rendering | None |
| `--dry-run` | Show what would be done without executing | False |
| `--verbose, -v` | Show detailed output with rich formatting | False |

#### Configuration Format

```yaml
# Basic configuration
repository: "owner/repo"          # GitHub repository in owner/repo format
file_path: "path/to/file.md"      # Path to file within repository
ref: "main"                       # Branch, tag, or commit SHA (optional)

# Advanced configuration
repository: "{{ org }}/{{ repo }}"  # Template variables
file_path: "{{ file_path }}"
ref: "{{ branch }}"
```

#### Examples

**Basic File Reading:**
```bash
# config.yaml
repository: "octocat/Hello-World"
file_path: "README.md"
ref: "main"

# Command
untaped github read-file config.yaml
```

**With Template Variables:**
```yaml
# config.yaml
repository: "{{ org }}/{{ repo }}"
file_path: "{{ file_path }}"

# vars.yaml
org: "microsoft"
repo: "vscode"
file_path: "README.md"

# Command
untaped github read-file --config-file config.yaml --vars-file vars.yaml
```

**Dry Run for Validation:**
```bash
untaped github read-file config.yaml --dry-run --verbose
```

#### Exit Codes

| Code | Description |
|------|-------------|
| 0 | Success |
| 1 | Validation error |
| 2 | Authentication error |
| 3 | Permission error |
| 4 | Network error |
| 5 | File not found |
| 6 | Configuration error |
| 7 | API error |
| 100 | Unknown error |

### github list-directory

List files in a GitHub repository directory.

#### Usage

```bash
untaped github list-directory [OPTIONS] CONFIG_FILE

# Examples
untaped github list-directory config.yaml
untaped github list-directory --recursive config.yaml
untaped github list-directory --dry-run config.yaml
```

#### Options

| Option | Description | Default |
|--------|-------------|---------|
| `--config-file PATH` | YAML configuration file path | Required |
| `--vars-file FILE` | Variable file(s) for template rendering | None |
| `--dry-run` | Show what would be done without executing | False |
| `--verbose, -v` | Show detailed output with rich formatting | False |
| `--recursive, -r` | List files recursively in subdirectories | False |

#### Configuration Format

```yaml
# Basic configuration
repository: "owner/repo"          # GitHub repository in owner/repo format
directory_path: "docs"            # Directory path (use "." for root)
recursive: false                  # List subdirectories recursively (optional)

# With templates
repository: "{{ org }}/{{ repo }}"
directory_path: "{{ path }}"
recursive: "{{ recursive_flag }}"
```

#### Examples

**List Root Directory:**
```yaml
# config.yaml
repository: "octocat/Hello-World"
directory_path: "."

# Command
untaped github list-directory config.yaml
```

**List with Recursion:**
```yaml
# config.yaml
repository: "microsoft/vscode"
directory_path: "src"
recursive: true

# Command
untaped github list-directory --recursive config.yaml
```

**With Template Variables:**
```yaml
# config.yaml
repository: "{{ org }}/{{ repo }}"
directory_path: "{{ path }}"

# vars.yaml
org: "kubernetes"
repo: "kubernetes"
path: ".github/workflows"

# Command
untaped github list-directory --config-file config.yaml --vars-file vars.yaml
```

## Configuration File Discovery

The commands automatically search for configuration files in this order:

1. **Explicit path**: `--config-file config.yaml`
2. **Environment variable**: `UNTAPED_GITHUB_CONFIG_FILE=/path/to/config.yaml`
3. **Default locations**:
   - `untaped-github.yaml`
   - `untaped-github.yml`
   - `.untaped-github.yaml`
   - `.untaped-github.yml`

## Template Variables

### Variable File Format

```yaml
# variables.yaml
org: "myorg"
repo: "myproject"
file_path: "README.md"
branch: "main"
```

### Environment Variables

Common environment variables for GitHub operations:

```bash
export GITHUB_TOKEN="your_token_here"
export GITHUB_USER="your_username"
export GITHUB_REPO="owner/repo"
export GITHUB_ORG="your_org"
```

### Template Syntax

```yaml
# config.yaml
repository: "{{ env.GITHUB_ORG }}/{{ env.GITHUB_REPO }}"
file_path: "{{ env.GITHUB_FILE_PATH }}"
ref: "{{ env.GITHUB_BRANCH | default('main') }}"
```

## Authentication

### Prerequisites

1. **Install GitHub CLI**:
   ```bash
   # macOS
   brew install gh

   # Ubuntu/Debian
   sudo apt install gh

   # Windows
   winget install --id GitHub.cli
   ```

2. **Authenticate with GitHub**:
   ```bash
   gh auth login
   ```

### Verification

```bash
# Check authentication status
untaped github read-file config.yaml --dry-run

# Or check manually
gh auth status
```

## Output Formats

### Verbose Mode (--verbose)

Rich formatted output with:
- Detailed configuration tables
- Progress indicators
- Structured error messages
- File content in bordered panels

### Standard Mode

Simple, clean output suitable for:
- Scripting and automation
- Log files
- CI/CD pipelines

## Error Handling

### Validation Errors

Configuration validation provides detailed error messages:

```bash
❌ Configuration validation failed:
  - repository: Invalid repository format: must be in owner/repo format
  - file_path: Path cannot contain '..' for security reasons
```

### Authentication Errors

Clear guidance for authentication issues:

```bash
❌ Authentication Error: Not logged in to GitHub CLI
   Please run: gh auth login
```

### Permission Errors

Detailed information about access requirements:

```bash
❌ Permission Error: Cannot access repository owner/repo
   Please check that:
   - The repository exists
   - You have access to the repository
   - Your GitHub token has the required permissions
```

## Best Practices

### Configuration Organization

```bash
project/
├── configs/
│   ├── prod-readme.yaml
│   ├── staging-config.yaml
│   └── dev-workflows.yaml
├── variables/
│   ├── prod-vars.yaml
│   ├── staging-vars.yaml
│   └── dev-vars.yaml
└── scripts/
    └── deploy.sh
```

### Security Considerations

1. **Never commit tokens or secrets** to version control
2. **Use environment variables** for sensitive data
3. **Validate configurations** with dry-run before execution
4. **Use specific file paths** to avoid exposing sensitive files

### Performance Optimization

1. **Use specific file paths** instead of listing directories when possible
2. **Cache configurations** for repeated operations
3. **Use appropriate ref values** (branches, tags, or commits)
4. **Batch operations** when reading multiple files

## Troubleshooting

### Common Issues

**"GitHub CLI 'gh' is not installed"**
```bash
# Install GitHub CLI
brew install gh  # macOS
sudo apt install gh  # Ubuntu
winget install --id GitHub.cli  # Windows
```

**"Not authenticated with GitHub CLI"**
```bash
gh auth login
```

**"Repository not found"**
```bash
# Check repository exists and is accessible
gh repo view owner/repo
```

**"File not found"**
```bash
# List directory contents first
untaped github list-directory config.yaml
```

**"Rate limit exceeded"**
```bash
# Check rate limit status
gh api rate_limit

# Wait for reset or use authenticated requests
gh auth login
```

### Debug Mode

Use verbose mode to see detailed execution information:

```bash
untaped github read-file config.yaml --verbose --dry-run
```

### Log Files

Error logs are available at:
- `logs/github-operations.log` - Detailed operation logs
- `htmlcov/index.html` - Test coverage reports (after running tests)

## Integration Examples

### CI/CD Pipeline

```yaml
# .github/workflows/untaped-check.yml
name: Validate untaped configurations
on: [push, pull_request]

jobs:
  validate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - name: Install untaped
        run: |
          curl -s https://raw.githubusercontent.com/your-org/untaped/main/install.sh | bash
      - name: Validate configurations
        run: |
          untaped github read-file config.yaml --dry-run
          untaped github list-directory config.yaml --dry-run
```

### Script Automation

```bash
#!/bin/bash
# validate-and-deploy.sh

set -e

echo "Validating GitHub configurations..."
untaped github read-file config.yaml --dry-run
untaped github list-directory config.yaml --dry-run

echo "Deploying configuration changes..."
untaped github read-file config.yaml
untaped github list-directory config.yaml

echo "Deployment complete!"
```

## Contributing

### Development Setup

1. **Clone and setup**:
   ```bash
   git clone <repository>
   cd untaped
   uv sync
   ```

2. **Run tests**:
   ```bash
   uv run --package untaped-github pytest
   ```

3. **Format code**:
   ```bash
   uv run --package untaped-github ruff format .
   ```

### Adding New Commands

1. Create command file in `packages/untaped-cli/src/untaped_cli/commands/`
2. Implement CLI interface using Typer
3. Add configuration models if needed
4. Write contract and integration tests
5. Update documentation

## Support

- **Issues**: GitHub Issues
- **Documentation**: Main untaped documentation
- **Community**: untaped community channels

---

*This documentation follows the untaped toolkit standards and conventions.*
