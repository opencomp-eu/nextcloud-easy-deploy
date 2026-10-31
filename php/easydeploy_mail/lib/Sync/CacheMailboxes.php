<?php

declare(strict_types=1);

namespace OCA\EasyDeployMail\Sync;

use OCA\Mail\Account;
use OCA\Mail\Db\Mailbox;
use OCA\Mail\Db\MailboxMapper;
use OCA\Mail\Service\AccountService;
use OCA\Mail\Service\Sync\SyncService;
use OCP\IUser;
use Psr\Log\LoggerInterface;
use Throwable;

/**
 * Message lists are served from Nextcloud's cache. Until a folder has sync
 * tokens, /apps/mail/api/messages answers HTTP 400. Fill that cache for every
 * folder that does not have it yet.
 */
class CacheMailboxes {
	/**
	 * @return array{cached: int, failed: list<string>}
	 */
	public static function cacheUser(IUser $user): array {
		$appManager = \OC::$server->get(\OCP\App\IAppManager::class);
		if (!$appManager->isInstalled('mail')) {
			return ['cached' => 0, 'failed' => []];
		}
		$appManager->loadApp('mail');
		/** @var AccountService $accounts */
		$accounts = \OC::$server->get(AccountService::class);
		/** @var MailboxMapper $mailboxes */
		$mailboxes = \OC::$server->get(MailboxMapper::class);
		/** @var SyncService $sync */
		$sync = \OC::$server->get(SyncService::class);
		$logger = \OC::$server->get(LoggerInterface::class);

		$cached = 0;
		$failed = [];
		foreach ($accounts->findByUserId($user->getUID()) as $account) {
			foreach ($mailboxes->findAll($account) as $mailbox) {
				$error = self::cacheMailbox($sync, $logger, $account, $mailbox);
				if ($error === null) {
					continue;
				}
				if ($error === '') {
					$cached++;
					continue;
				}
				$failed[] = $error;
			}
		}
		return ['cached' => $cached, 'failed' => $failed];
	}

	/**
	 * @return string|null null when there was nothing to cache, '' when it
	 *                     cached, or the error text when sync failed
	 */
	private static function cacheMailbox(SyncService $sync, LoggerInterface $logger, Account $account, Mailbox $mailbox): ?string {
		if ($mailbox->isCached() || $mailbox->getSelectable() === false) {
			return null;
		}
		try {
			$sync->syncMailbox(
				$account,
				$mailbox,
				\Horde_Imap_Client::SYNC_NEWMSGSUIDS | \Horde_Imap_Client::SYNC_FLAGSUIDS | \Horde_Imap_Client::SYNC_VANISHEDUIDS,
				false,
				null
			);
			return '';
		} catch (Throwable $e) {
			$message = $mailbox->getName() . ': ' . $e->getMessage();
			$logger->warning(
				'Could not cache mailbox ' . $message,
				['app' => 'easydeploy_mail', 'exception' => $e]
			);
			return $message;
		}
	}
}
