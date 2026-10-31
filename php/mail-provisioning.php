<?php
// Create or update one Nextcloud Mail provisioning config, then provision
// every matching user. The config arrives as JSON on stdin (it includes the
// master password). Released Mail versions have no occ command for this.
// Usage: php /opt/easydeploy/mail-provisioning.php < config.json
declare(strict_types=1);

require_once '/var/www/html/lib/base.php';

use OCA\Mail\Service\Provisioning\Manager;

$data = json_decode((string)stream_get_contents(STDIN), true);
if (!is_array($data) || empty($data['provisioningDomain'])) {
	fwrite(STDERR, "mail-provisioning: expected a provisioning config with provisioningDomain\n");
	exit(2);
}

$appManager = \OC::$server->get(\OCP\App\IAppManager::class);
if (!$appManager->isEnabledForUser('mail')) {
	fwrite(STDERR, "mail-provisioning: the mail app is not enabled\n");
	exit(3);
}
$appManager->loadApp('mail');

/** @var Manager $manager */
$manager = \OC::$server->get(Manager::class);

$existing = null;
foreach ($manager->getConfigs() as $config) {
	if (strcasecmp($config->getProvisioningDomain(), (string)$data['provisioningDomain']) === 0) {
		$existing = $config;
		break;
	}
}

if ($existing === null) {
	$manager->newProvisioning($data);
	echo "created\n";
} else {
	$data['id'] = $existing->getId();
	$manager->updateProvisioning($data);
	echo "updated\n";
}

$count = $manager->provision();
echo "provisioned:{$count}\n";

// Kanidm sign-in has no Nextcloud password. Mail's own middleware then skips
// storing the master password (it gives up when login credentials are
// unavailable), and IMAP is attempted with an empty password. Apply the
// master password here for everyone who already has an account. New sign-ins
// are covered by the easydeploy_mail app.
$configs = $manager->getConfigs();
$userManager = \OC::$server->get(\OCP\IUserManager::class);
$userManager->callForAllUsers(function (\OCP\IUser $user) use ($manager, $configs): void {
	$manager->updatePassword($user, null, $configs);
});
echo "passwords:set\n";
