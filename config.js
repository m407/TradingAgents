// Local check: npx renovate --platform=local
module.exports = {
  onboarding: false,
  requireConfig: 'ignored',
  enabledManagers: ['pep621'],
  pep621: {
    managerFilePatterns: ['/^pyproject\\.toml$/'],
  },
  packageRules: [
    {
      matchManagers: ['pep621'],
      rangeStrategy: 'bump',
    },
    {
      matchManagers: ['pep621'],
      matchDepTypes: ['requires-python'],
      enabled: false,
    },
  ],
};
