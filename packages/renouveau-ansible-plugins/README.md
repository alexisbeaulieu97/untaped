# Renouveau Ansible Plugins

Collection of custom Ansible plugins for manipulating dictionaries, lists, strings, etc.

## Installation

Add to your ansible.cfg:
[defaults]
filter_plugins = /path/to/renouveau-ansible-plugins/src/renouveau_ansible_plugins/filter_plugins

## Usage Examples

- dict_set: {{ {'a': {}} | dict_set('a.b', 'value') }} → {'a': {'b': 'value'}}

More details in plugin docs.
