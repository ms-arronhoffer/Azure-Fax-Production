using './private-test.bicep'
param location = 'westus2'
param namePrefix = 'faxe2e'
param runtimeResourceGroupName = 'rg-fax-e2e-westus2'
param authoringResourceGroupName = 'rg-fax-authoring-e2e-westus2'
param apiClientId = '00000000-0000-0000-0000-000000000000'
// Optional, separately approved human access. These do not grant directory roles or consent.
param humanReviewClientApplicationIds = []
param humanReviewPrincipalObjectIds = []
param runnerSshPublicKey = 'ssh-ed25519 REPLACE_WITH_PUBLIC_KEY_ONLY'
param alertEmail = 'REPLACE_WITH_MONITORED_EMAIL'
param maximumInstanceCount = 1
