<?php

declare(strict_types=1);

namespace OCA\EasyDeployMail\Listener;

use Psr\Log\LoggerInterface;

/**
 * Ask the host to create this person's Stalwart mailbox, and wait until that
 * finishes. Mail's first IMAP login happens after sign-in returns, so the
 * mailbox has to exist before this request ends.
 */
class EnsureMailbox {
	private const WAIT_SECONDS = 20;

	public function waitForMailbox(): void {
		$config = \OC::$server->get(\OCP\IConfig::class);
		if ($config->getAppValue('easydeploy_mail', 'mailbox_sync', 'no') !== 'yes') {
			return;
		}
		$data = rtrim($config->getSystemValueString('datadirectory', ''), '/');
		if ($data === '') {
			return;
		}
		$requests = $data . '/easydeploy-mail-sync/requests';
		if (!is_dir($requests) && !@mkdir($requests, 0770, true) && !is_dir($requests)) {
			return;
		}
		$stamp = bin2hex(random_bytes(8));
		if (file_put_contents($requests . '/' . $stamp, $stamp) === false) {
			return;
		}
		$done = $data . '/easydeploy-mail-sync/done/' . $stamp;
		$deadline = microtime(true) + self::WAIT_SECONDS;
		while (microtime(true) < $deadline) {
			if (is_file($done)) {
				@unlink($done);
				return;
			}
			usleep(200000);
		}
		\OC::$server->get(LoggerInterface::class)->warning(
			'Stalwart had not created the mailbox within ' . self::WAIT_SECONDS . 's of sign-in',
			['app' => 'easydeploy_mail']
		);
	}
}
