<?php

declare(strict_types=1);

namespace OCA\EasyDeployMail\Listener;

use OCP\EventDispatcher\Event;
use OCP\EventDispatcher\IEventListener;
use OCP\User\Events\UserLoggedInEvent;
use Psr\Log\LoggerInterface;
use Throwable;

/**
 * Kanidm sign-in does not put a password in the Nextcloud session. Mail's
 * provisioning middleware then returns before it can copy the Stalwart master
 * password onto the mailbox, and the account stays on an empty IMAP password.
 *
 * @template-implements IEventListener<UserLoggedInEvent>
 */
class ProvisionMailPassword implements IEventListener {
	public function handle(Event $event): void {
		if (!($event instanceof UserLoggedInEvent)) {
			return;
		}
		$user = $event->getUser();
		try {
			$appManager = \OC::$server->get(\OCP\App\IAppManager::class);
			if (!$appManager->isInstalled('mail')) {
				return;
			}
			$appManager->loadApp('mail');
			/** @var \OCA\Mail\Service\Provisioning\Manager $manager */
			$manager = \OC::$server->get(\OCA\Mail\Service\Provisioning\Manager::class);
			$configs = $manager->getConfigs();
			if ($configs === []) {
				return;
			}
			$manager->provisionSingleUser($configs, $user);
			// null: there is no Nextcloud password. Mail substitutes the master password.
			$manager->updatePassword($user, null, $configs);
		} catch (Throwable $e) {
			\OC::$server->get(LoggerInterface::class)->warning(
				'Could not store the Stalwart master password for ' . $user->getUID() . ': ' . $e->getMessage(),
				['app' => 'easydeploy_mail', 'exception' => $e]
			);
		}
	}
}
