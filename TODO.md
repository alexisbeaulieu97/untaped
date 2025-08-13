# Renouveau Development Roadmap

This document tracks the development progress and upcoming features for the Renouveau Ansible project management tool.

## 🎯 Current Milestone: MVP Foundation

### ✅ Completed Tasks

#### Phase 1: Project Setup & Architecture
- [x] **UV Workspace Setup**: Create UV workspace with packages/ structure
- [x] **Package Architecture**: Define renouveau-schema, renouveau-core, renouveau-app packages
- [x] **Schema Framework**: YAML-based API definition framework
- [x] **Tower API Schemas**: Define Ansible Tower API v2 schemas (job_templates, inventories, projects)
- [x] **Project Documentation**: Comprehensive README with vision and architecture

#### Phase 2: Schema Foundation
- [x] **Schema Loader**: Framework for loading and parsing YAML API definitions
- [x] **Generic API Client**: Schema-driven HTTP client with validation
- [x] **Tower Schemas**: Complete YAML definitions for core Tower endpoints
- [x] **Exception Handling**: Structured error handling for schema operations

### 🚧 In Progress

#### Phase 3: Core Implementation
- [ ] **Core API Clients**: Implement Tower and GitHub clients using schema framework
- [ ] **Configuration System**: Pydantic-based configuration with environment support
- [ ] **Ansible Discovery**: Local Ansible project structure detection

### 📋 Next Up

#### Phase 4: CLI Interface
- [ ] **Typer CLI Setup**: Base CLI application with Rich integration
- [ ] **Tower Commands**: Implement tower subcommands (job-template, inventory, project)
- [ ] **Local Commands**: Implement ansible subcommands (playbook, role)
- [ ] **GitHub Commands**: Basic GitHub repository operations

## 🚀 Detailed Feature Roadmap

### 🔧 Core Features (v0.1.0)

#### Schema System
- [x] **YAML Schema Definitions**: Structured API endpoint definitions
- [x] **Schema Validation**: Request/response validation using schemas
- [x] **Generic Client Framework**: Reusable API client driven by schemas
- [ ] **Schema Testing**: Unit tests for schema loading and validation

#### Ansible Tower Integration
- [x] **Job Templates API**: List, get, launch job templates
- [x] **Inventories API**: List inventories, hosts, groups
- [x] **Projects API**: List projects, trigger updates, get playbooks
- [x] **Workflow Job Templates API**: List, get workflow templates and nodes
- [x] **Workflow Analysis**: Find workflows that use specific job templates
- [ ] **Jobs API**: Monitor running jobs, get job output
- [ ] **Organizations API**: List and manage organizations
- [ ] **Credentials API**: List and manage credentials

#### Local Ansible Management
- [ ] **Project Discovery**: Auto-detect Ansible project structure
- [ ] **Playbook Operations**: List, validate, analyze playbooks
- [ ] **Role Management**: List, validate, analyze roles
- [ ] **Inventory Parsing**: Parse static inventory files
- [ ] **Variable Discovery**: Find and parse group_vars, host_vars

#### CLI Interface
- [x] **Base CLI Application**: Typer-based command structure
- [x] **Rich UI Components**: Beautiful tables, progress bars, status displays
- [x] **Configuration Commands**: Setup and manage configuration
- [x] **Interactive Prompts**: User-friendly input collection
- [x] **Tower Commands**: Complete Tower/AWX command implementation
- [x] **Workflow Commands**: Workflow management and dependency analysis

### 🌟 Enhanced Features (v0.2.0)

#### GitHub Integration
- [ ] **Repository Discovery**: Find Ansible-related repositories
- [ ] **Role Repositories**: Clone and manage Ansible role repositories
- [ ] **Playbook Repositories**: Clone and manage playbook repositories
- [ ] **Branch Management**: Switch branches, create feature branches
- [ ] **Content Synchronization**: Sync local content with remote repositories

#### Advanced Tower Operations
- [ ] **Workflow Templates**: Manage workflow job templates
- [ ] **Survey Support**: Handle job template surveys
- [ ] **Scheduling**: Create and manage job schedules
- [ ] **Teams & Permissions**: Manage team access and permissions
- [ ] **Custom Credentials**: Create and manage custom credential types

#### Content Generation
- [ ] **Project Scaffolding**: Generate new Ansible project structure
- [ ] **Role Generation**: Create new roles with proper structure
- [ ] **Playbook Templates**: Generate playbooks from templates
- [ ] **Best Practices**: Enforce Ansible best practices in generated content

### 🔬 Advanced Features (v0.3.0)

#### Testing Integration
- [ ] **Molecule Integration**: Run Molecule tests from CLI
- [ ] **Ansible Lint**: Integrate ansible-lint for validation
- [ ] **Test Reporting**: Beautiful test result displays
- [ ] **CI Integration**: Generate CI/CD pipeline configurations

#### Multi-Environment Support
- [ ] **Environment Profiles**: Manage multiple Tower instances
- [ ] **Environment Promotion**: Promote content between environments
- [ ] **Configuration Inheritance**: Environment-specific configuration overrides
- [ ] **Secret Management**: Secure handling of environment-specific secrets

#### Workflow Automation
- [ ] **Pipeline Definitions**: Define complex workflows in YAML
- [ ] **Dependency Management**: Handle role and collection dependencies
- [ ] **Automated Deployment**: End-to-end deployment workflows
- [ ] **Rollback Support**: Automated rollback capabilities

### 🌍 Future Enhancements (v1.0.0+)

#### Enterprise Features
- [ ] **AWX Integration**: Full support for AWX (open-source)
- [ ] **LDAP/SSO Support**: Enterprise authentication integration
- [ ] **Audit Logging**: Comprehensive audit trail
- [ ] **Backup/Restore**: Tower configuration backup and restore

#### Performance & Scale
- [ ] **Async Operations**: Async API client for better performance
- [ ] **Caching**: Intelligent caching of API responses
- [ ] **Bulk Operations**: Efficient bulk API operations
- [ ] **Large Project Support**: Handle large Ansible projects efficiently

#### Integrations
- [ ] **GitLab Support**: GitLab repository integration
- [ ] **Bitbucket Support**: Bitbucket repository integration
- [ ] **Slack/Teams**: Notification integrations
- [ ] **Monitoring**: Integration with monitoring systems

## 🏗️ Technical Debt & Improvements

### Code Quality
- [ ] **Test Coverage**: Achieve 90%+ test coverage
- [ ] **Type Annotations**: Complete type annotations across all packages
- [ ] **Documentation**: Comprehensive API documentation
- [ ] **Performance Profiling**: Identify and fix performance bottlenecks

### Developer Experience
- [ ] **Plugin System**: Allow third-party extensions
- [ ] **Configuration Validation**: Better configuration error messages
- [ ] **Debugging Support**: Enhanced debugging and logging
- [ ] **Auto-completion**: Shell completion for CLI commands

### Deployment & Distribution
- [ ] **PyPI Package**: Publish to PyPI
- [ ] **Docker Image**: Official Docker image
- [ ] **GitHub Actions**: CI/CD pipeline
- [ ] **Release Automation**: Automated release process

## 📅 Release Schedule

### v0.1.0 - Foundation (Target: 4 weeks)
- Core schema system
- Basic Tower API integration
- Simple CLI interface
- Local Ansible discovery

### v0.2.0 - Integration (Target: 8 weeks)  
- GitHub integration
- Enhanced Tower features
- Content generation
- Improved CLI UX

### v0.3.0 - Advanced (Target: 12 weeks)
- Testing integration
- Multi-environment support
- Workflow automation
- Performance improvements

### v1.0.0 - Production Ready (Target: 16 weeks)
- Enterprise features
- Full documentation
- Comprehensive testing
- Stable API

## 🎯 Success Metrics

### User Adoption
- [ ] 100+ GitHub stars
- [ ] 10+ contributors
- [ ] 1000+ PyPI downloads/month
- [ ] Active community discussions

### Feature Completeness
- [ ] Cover 80% of common Tower operations
- [ ] Support major Git platforms
- [ ] Handle complex Ansible project structures
- [ ] Provide excellent user experience

### Code Quality
- [ ] 90%+ test coverage
- [ ] Zero critical security vulnerabilities
- [ ] Sub-second response time for common operations
- [ ] Comprehensive documentation

---

**Last Updated**: January 2024  
**Maintainer**: Alexis Beaulieu  

For questions or suggestions about the roadmap, please open a [GitHub Discussion](https://github.com/yourusername/renouveau/discussions).
