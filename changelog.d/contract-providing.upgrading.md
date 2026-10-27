Replace `providing(owner, {"house": [HouseRules()]})` with
`providing({"house": (HouseRules,)})`, and make each class a `Provider`
(`class HouseRules(Rules)` already is one: lint's `Rules` is a `Provider`).
