<?php

declare(strict_types=1);

namespace OCA\EasyDeployMail\Listener;

use OCP\AppFramework\Http\Events\BeforeTemplateRenderedEvent;
use OCP\EventDispatcher\Event;
use OCP\EventDispatcher\IEventListener;
use OCP\IInitialState;

/**
 * Mail treats a Kanidm session as "no password" and then skips syncing every
 * provisioned account. The folder stays uncached, so the message list request
 * keeps returning HTTP 400. The IMAP password is already the Stalwart master
 * password; tell the page that a password is available.
 *
 * @template-implements IEventListener<BeforeTemplateRenderedEvent>
 */
class AllowProvisionedSync implements IEventListener {
	public function __construct(
		private IInitialState $initialState,
	) {
	}

	public function handle(Event $event): void {
		if (!($event instanceof BeforeTemplateRenderedEvent)) {
			return;
		}
		if ($event->getResponse()->getApp() !== 'mail') {
			return;
		}
		$this->initialState->provideInitialState('mail', 'password-is-unavailable', false);
	}
}
