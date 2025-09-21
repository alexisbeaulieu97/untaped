# Manual Integration Test Validation Checklist

## Overview

This checklist provides manual validation steps for the GitHub API support feature. These tests should be run to ensure the implementation works correctly in real-world scenarios.

## Prerequisites

1. **GitHub CLI installed and authenticated**:
   ```bash
   gh auth login
   gh auth status  # Should show "Logged in to github.com"
   ```

2. **Test repositories available**:
   - Access to `octocat/Hello-World` (public repository)
   - Access to `microsoft/vscode` (public repository for larger files)
   - Optionally: Access to private repositories for testing permissions

## Test Scenarios

### ✅ 1. Basic File Reading
```bash
# Test 1a: Read README from Hello-World
cat > test-config-1.yaml << 'EOF'
repository: "octocat/Hello-World"
file_path: "README.md"
ref: "main"
EOF

untaped github read-file test-config-1.yaml

# Expected: Should display README content with success message
```

```bash
# Test 1b: Read file with custom branch/tag
cat > test-config-2.yaml << 'EOF'
repository: "octocat/Hello-World"
file_path: "README.md"
ref: "main"
EOF

untaped github read-file test-config-2.yaml --verbose

# Expected: Rich formatted output with file details
```

### ✅ 2. Directory Listing
```bash
# Test 2a: List root directory
cat > test-list-1.yaml << 'EOF'
repository: "octocat/Hello-World"
directory_path: "."
EOF

untaped github list-directory test-list-1.yaml

# Expected: Should show files in the root directory
```

```bash
# Test 2b: List with recursion
cat > test-list-2.yaml << 'EOF'
repository: "microsoft/vscode"
directory_path: "."
recursive: true
EOF

untaped github list-directory test-list-2.yaml --recursive

# Expected: Should show files from subdirectories recursively (all levels)
```

### ✅ 3. Template Variables
```bash
# Test 3a: Variable file support
cat > test-template-config.yaml << 'EOF'
repository: "{{ org }}/{{ repo }}"
file_path: "{{ file_path }}"
ref: "{{ branch }}"
EOF

cat > test-template-vars.yaml << 'EOF'
org: "octocat"
repo: "Hello-World"
file_path: "README.md"
branch: "main"
EOF

untaped github read-file --config-file test-template-config.yaml --vars-file test-template-vars.yaml

# Expected: Should read file using template variables
```

```bash
# Test 3b: Multiple variable files
cat > test-repo-vars.yaml << 'EOF'
org: "microsoft"
repo: "vscode"
EOF

cat > test-path-vars.yaml << 'EOF'
file_path: "README.md"
branch: "main"
EOF

untaped github read-file --config-file test-template-config.yaml --vars-file test-repo-vars.yaml --vars-file test-path-vars.yaml

# Expected: Should merge variables from multiple files
```

### ✅ 4. Configuration Discovery
```bash
# Test 4a: Environment variable config
export UNTAPED_GITHUB_CONFIG_FILE=test-config-1.yaml
untaped github read-file
unset UNTAPED_GITHUB_CONFIG_FILE

# Expected: Should find config file via environment variable
```

```bash
# Test 4b: Default config file discovery
cp test-config-1.yaml untaped-github.yaml
untaped github read-file
rm untaped-github.yaml

# Expected: Should find config file in current directory
```

### ✅ 5. Dry Run Validation
```bash
# Test 5a: Valid configuration dry run
untaped github read-file test-config-1.yaml --dry-run --verbose

# Expected: Should show validation details and "can proceed"
```

```bash
# Test 5b: Invalid configuration dry run
cat > invalid-config.yaml << 'EOF'
repository: "invalid/repo/format"
file_path: "missing-file.md"
EOF

untaped github read-file invalid-config.yaml --dry-run

# Expected: Should show validation errors and exit with code 1
```

```bash
# Test 5c: Non-existent file dry run
cat > missing-file.yaml << 'EOF'
repository: "octocat/Hello-World"
file_path: "nonexistent-file.md"
EOF

untaped github read-file missing-file.yaml --dry-run --verbose

# Expected: Should show "File Exists: ❌ No" and "can_proceed: false"
```

### ✅ 6. Error Handling
```bash
# Test 6a: File not found
cat > test-missing.yaml << 'EOF'
repository: "octocat/Hello-World"
file_path: "nonexistent-file.md"
EOF

untaped github read-file test-missing.yaml

# Expected: Should show file not found error with exit code 5
```

```bash
# Test 6b: Private repository (if available)
cat > test-private.yaml << 'EOF'
repository: "your-org/private-repo"
file_path: "README.md"
EOF

untaped github read-file test-private.yaml

# Expected: Should show permission error with exit code 3
```

### ✅ 7. Authentication Testing
```bash
# Test 7a: Authentication verification
untaped github read-file test-config-1.yaml --dry-run

# Expected: Should work without re-authentication if already logged in
```

```bash
# Test 7b: Force authentication check (if needed)
gh auth logout
untaped github read-file test-config-1.yaml

# Expected: Should fail with authentication error and helpful message
```

### ✅ 8. Performance Validation
```bash
# Test 8a: Multiple operations
time untaped github read-file test-config-1.yaml
time untaped github list-directory test-list-1.yaml

# Expected: Each operation should complete in reasonable time (< 5 seconds)
```

```bash
# Test 8b: Large file handling
cat > test-large.yaml << 'EOF'
repository: "microsoft/vscode"
file_path: "README.md"
EOF

time untaped github read-file test-large.yaml

# Expected: Should handle larger files without memory issues
```

### ✅ 9. CLI Integration
```bash
# Test 9a: Help command
untaped github read-file --help
untaped github list-directory --help
untaped github --help

# Expected: Should show proper help text for all commands
```

```bash
# Test 9b: Version command
untaped --version

# Expected: Should show version information
```

### ✅ 10. Edge Cases
```bash
# Test 10a: Special characters in file paths
cat > test-special.yaml << 'EOF'
repository: "octocat/Hello-World"
file_path: "docs/api-reference.md"
EOF

untaped github read-file test-special.yaml

# Expected: Should handle special characters properly
```

```bash
# Test 10b: Deep directory paths
cat > test-deep.yaml << 'EOF'
repository: "microsoft/vscode"
directory_path: ".github/workflows"
recursive: true
EOF

untaped github list-directory test-deep.yaml --recursive

# Expected: Should handle deep directory structures
```

## Cleanup

```bash
# Remove test files
rm -f test-config-*.yaml test-list-*.yaml test-template-*.yaml
rm -f test-*.yaml invalid-config.yaml
```

## Success Criteria

All tests should pass with:
- ✅ Proper exit codes (0 for success, appropriate error codes for failures)
- ✅ Informative error messages with actionable guidance
- ✅ Rich formatted output in verbose mode
- ✅ Reasonable performance (< 5 seconds for most operations)
- ✅ Proper authentication handling
- ✅ Template variable support working correctly
- ✅ Configuration file discovery working properly

## Reporting

If any tests fail:
1. Document the failure scenario
2. Capture the error output
3. Check the logs at `logs/github-operations.log`
4. Report issues with reproduction steps

## Notes

- Some tests may require specific repository access permissions
- Network connectivity is required for all tests
- Rate limiting may affect rapid successive tests
- Authentication status should be verified before testing
