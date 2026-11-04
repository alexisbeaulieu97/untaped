`untaped ansible impact checkouts ROLE` lists what checks out each dependent
of a role, from every plugin that fills ansible's new `CheckoutSource`
contract: awx fills it with its job templates, so installing awx now installs
ansible, too.
