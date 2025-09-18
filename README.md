# Untaped - Ansible Project Management Tool

**Untaped** is a modern command-line tool for managing Ansible projects, designed to streamline interactions with Ansible Tower/AWX, Git repositories, and local Ansible content.

## 🎯 Vision

Renouveau aims to be the Swiss Army knife for Ansible developers and operators, providing:

- **Unified Interface**: Single CLI tool for all Ansible project management tasks
- **Schema-Driven API**: YAML-defined API schemas for consistent and extensible integrations
- **Modern Tooling**: Built with Python UV workspaces, Typer, Rich, and Pydantic
- **Git Integration**: Seamless interaction with GitHub repositories for roles and playbooks
- **Tower/AWX Management**: Full lifecycle management of job templates, inventories, and projects

## 🏗️ Architecture

Renouveau uses a UV workspace structure with three main packages:

```
renouveau/
├── packages/
│   ├── renouveau-schema/      # YAML API definitions & framework
│   ├── renouveau-core/        # Business logic & API clients  
│   └── renouveau-app/         # CLI interface (typer + rich)
├── pyproject.toml             # Workspace configuration
├── uv.lock                    # Shared dependency lock
└── main.py                    # Entry point
```

### Package Overview

#### 🔧 **renouveau-schema**
- **YAML API Definitions**: Structured schemas for Ansible Tower/AWX API v2
- **Generic Framework**: Reusable API client framework driven by YAML schemas
- **Validation**: Request/response validation using schema definitions

#### 🧠 **renouveau-core** 
- **API Clients**: Tower/AWX and GitHub API clients built on the schema framework
- **Business Logic**: Core functionality for Ansible project management
- **Configuration**: Application settings and environment management

#### 🖥️ **renouveau-app**
- **CLI Interface**: Beautiful command-line interface using Typer and Rich
- **Commands**: Organized subcommands for different Ansible operations
- **User Experience**: Progress bars, colored output, and interactive prompts

## 🚀 Features

### Current Capabilities

#### Ansible Tower/AWX Integration
- **Job Templates**: List, view, and launch job templates
- **Inventories**: Browse inventories, hosts, and groups
- **Projects**: Manage SCM projects and trigger updates
- **Workflow Templates**: List and analyze workflow job templates
- **Dependency Analysis**: Find workflows that use specific job templates
- **Real-time Status**: Monitor job execution and project sync status

#### Local Ansible Management
- **Project Discovery**: Auto-detect Ansible project structure
- **Content Validation**: Syntax checking and linting
- **Role Management**: List and analyze local roles
- **Playbook Operations**: Discover and validate playbooks

#### Git Repository Integration
- **Authentication**: Uses local Git configuration and SSH keys
- **Repository Discovery**: Find and interact with Ansible-related repositories
- **Content Synchronization**: Keep local content in sync with remote repositories

### Planned Features

- **Content Generation**: Scaffold new roles, playbooks, and projects
- **Dependency Management**: Ansible Galaxy and collection dependencies
- **Testing Integration**: Molecule test execution and management
- **Multi-Environment**: Support for dev/staging/prod Tower instances
- **Workflow Automation**: Chain operations across multiple systems

## 📋 Usage

### Installation

```bash
# Clone the repository
git clone https://github.com/yourusername/renouveau.git
cd renouveau

# Install using UV
uv sync --all-packages

# Run the CLI
uv run renouveau --help
```

### Configuration

Create a configuration file to define your Ansible project structure and Tower endpoints:

```yaml
# ~/.config/renouveau/config.yaml
ansible:
  project_root: "."
  playbook_dirs: ["playbooks/"]
  role_dirs: ["roles/"]
  inventory_dirs: ["inventory/"]

tower:
  url: "https://tower.example.com"
  verify_ssl: true
  
github:
  organization: "your-org"
  token_source: "env:GITHUB_TOKEN"  # or "file:~/.github_token"
```

### Command Examples

```bash
# Ansible Tower operations
renouveau tower job-template list
renouveau tower job-template show 42
renouveau tower job-template launch 42 --extra-vars '{"key": "value"}'
renouveau tower job-template workflows 42  # Find workflows using job template

renouveau tower inventory list --search "production"
renouveau tower inventory hosts 10

renouveau tower project list
renouveau tower project update 5

renouveau tower workflow list
renouveau tower workflow show 15
renouveau tower workflow nodes 15  # List workflow nodes

# Local Ansible management
renouveau ansible playbook list
renouveau ansible playbook validate site.yml
renouveau ansible role list
renouveau ansible role validate common

# GitHub integration  
renouveau github repo list-roles
renouveau github repo clone ansible-role-nginx
renouveau github repo sync-local

# Project management
renouveau project init --template basic
renouveau project validate
renouveau project deploy --environment staging
```

## 🛠️ Development

### Prerequisites

- **Python 3.12+**: Modern Python with latest features
- **UV**: Fast Python package manager and project manager
- **Git**: For repository operations

### Development Setup

```bash
# Clone and setup
git clone https://github.com/yourusername/renouveau.git
cd renouveau

# Sync all workspace packages
uv sync --all-packages

# Install development dependencies
uv add --dev pytest mypy ruff black

# Run tests
uv run pytest

# Code formatting
uv run ruff format .
uv run ruff check .

# Type checking
uv run mypy packages/
```

### Project Structure

```
renouveau/
├── packages/
│   ├── renouveau-schema/
│   │   ├── src/renouveau_schema/
│   │   │   ├── framework/          # Generic API framework
│   │   │   ├── tower/              # Tower API schemas
│   │   │   └── github/             # GitHub API schemas
│   │   └── pyproject.toml
│   ├── renouveau-core/
│   │   ├── src/renouveau_core/
│   │   │   ├── tower/              # Tower API client
│   │   │   ├── github/             # GitHub API client
│   │   │   ├── ansible/            # Local Ansible tools
│   │   │   └── config/             # Configuration management
│   │   └── pyproject.toml
│   └── renouveau-app/
│       ├── src/renouveau_app/
│       │   ├── commands/           # CLI command groups
│       │   ├── ui/                 # Rich UI components
│       │   └── main.py             # CLI entry point
│       └── pyproject.toml
├── tests/                          # Integration tests
├── docs/                           # Documentation
├── examples/                       # Example configurations
├── .cursorrules                    # AI assistant guidelines
├── TODO.md                         # Development roadmap
└── pyproject.toml                  # Workspace root
```

### Adding New Features

1. **API Endpoints**: Add YAML schema definitions in `renouveau-schema`
2. **Business Logic**: Implement functionality in `renouveau-core`
3. **CLI Commands**: Add user interface in `renouveau-app`
4. **Tests**: Write tests for all new functionality
5. **Documentation**: Update README and inline docs

### Schema-Driven Development

Renouveau uses YAML schemas to define API interactions:

```yaml
# packages/renouveau-schema/src/renouveau_schema/tower/job_templates.yaml
name: "Ansible Tower Job Templates API"
version: "v2"
base_path: "/api/v2/job_templates"

endpoints:
  list:
    method: GET
    path: "/"
    description: "List all job templates"
    parameters:
      query:
        page:
          type: integer
          description: "Page number for pagination"
    response:
      type: object
      properties:
        count:
          type: integer
        results:
          type: array
          items:
            $ref: "#/components/schemas/JobTemplate"
```

This schema-driven approach ensures:
- **Consistency**: All API interactions follow the same patterns
- **Validation**: Request/response validation automatically handled
- **Documentation**: API structure is self-documenting
- **Extensibility**: New APIs can be added without code changes

## 🤝 Contributing

We welcome contributions! Please see our [Contributing Guidelines](CONTRIBUTING.md) for details.

### Development Philosophy

- **Schema-First**: Define APIs in YAML before implementing
- **UV-Native**: Use UV commands for all dependency management
- **Type Safety**: Leverage Python typing and Pydantic models
- **User Experience**: Prioritize clear, beautiful CLI interactions
- **Extensibility**: Design for easy addition of new APIs and features

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## 🔗 Links

- **Documentation**: [docs.renouveau.dev](https://docs.renouveau.dev)
- **PyPI Package**: [pypi.org/project/renouveau](https://pypi.org/project/renouveau)
- **Issue Tracker**: [GitHub Issues](https://github.com/yourusername/renouveau/issues)
- **Discussions**: [GitHub Discussions](https://github.com/yourusername/renouveau/discussions)

---

**Renouveau** - Bringing renewal to Ansible project management 🚀
