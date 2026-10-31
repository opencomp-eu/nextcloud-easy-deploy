<?php

declare(strict_types=1);

namespace OCA\EasyDeployMail\Listener;

use OCP\AppFramework\Http\Events\BeforeTemplateRenderedEvent;
use OCP\AppFramework\Http\TemplateResponse;
use OCP\EventDispatcher\Event;
use OCP\EventDispatcher\IEventListener;
use Psr\Log\LoggerInterface;
use Throwable;

/**
 * Mail skips syncing a provisioned account when the Nextcloud session has no
 * password. Kanidm sign-in never has one, so the inbox stays empty even after
 * the Stalwart master password is stored. Tell the Mail page that a password
 * is available. The password itself stays on the server.
 *
 * OCP\IInitialState no longer exists. OCP\IInitialStateService is the service
 * that can set another app's state.
 *
 * @template-implements IEventListener<BeforeTemplateRenderedEvent>
 */
class AllowProvisionedSync implements IEventListener {
	public function handle(Event $event): void {
		if (!($event instanceof BeforeTemplateRenderedEvent)) {
			return;
		}
		$response = $event->getResponse();
		if (!($response instanceof TemplateResponse) || $response->getApp() !== 'mail') {
			return;
		}
		if (!interface_exists(\OCP\IInitialStateService::class)) {
			return;
		}
		try {
			if (!$this->masterPasswordConfigured()) {
				return;
			}
			\OC::$server->get(\OCP\IInitialStateService::class)->provideInitialState(
				'mail',
				'password-is-unavailable',
				false
			);
		} catch (Throwable $e) {
			\OC::$server->get(LoggerInterface::class)->warning(
				'Could not let Mail sync the provisioned account: ' . $e->getMessage(),
				['app' => 'easydeploy_mail', 'exception' => $e]
			);
		}
	}

	private function masterPasswordConfigured(): bool {
		$appManager = \OC::$server->get(\OCP\App\IAppManager::class);
		if (!$appManager->isInstalled('mail')) {
			return false;
		}
		$appManager->loadApp('mail');
		/** @var \OCA\Mail\Service\Provisioning\Manager $manager */
		$manager = \OC::$server->get(\OCA\Mail\Service\Provisioning\Manager::class);
		foreach ($manager->getConfigs() as $config) {
			if ($config->getMasterPasswordEnabled() === true && ($config->getMasterPassword() ?? '') !== '') {
				return true;
			}
		}
		return false;
	}
}
