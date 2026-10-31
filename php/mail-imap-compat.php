<?php
// Stalwart advertises QRESYNC. Horde then waits for an untagged CLOSED
// response before it will treat the folder as selected. The first SELECT does
// not send that response, the folder is never marked cached, and
// /apps/mail/api/messages stays HTTP 400. Ignore the capability.
// Usage: php /opt/easydeploy/mail-imap-compat.php
declare(strict_types=1);

require_once '/var/www/html/lib/base.php';

$appManager = \OC::$server->get(\OCP\App\IAppManager::class);
if (!$appManager->isInstalled('mail')) {
	fwrite(STDERR, "mail-imap-compat: the mail app is not installed\n");
	exit(3);
}

$path = $appManager->getAppPath('mail') . '/lib/IMAP/IMAPClientFactory.php';
$source = file_get_contents($path);
if ($source === false) {
	fwrite(STDERR, "mail-imap-compat: cannot read {$path}\n");
	exit(4);
}

$marker = "\$params['capability_ignore'] = ['QRESYNC'];";
if (str_contains($source, $marker)) {
	echo "already\n";
	exit(0);
}

$count = 0;
$updated = preg_replace_callback(
	'/^([ \t]*)if \(\$account->getMailAccount\(\)->getAuthMethod\(\) === \'xoauth2\'\) \{/m',
	static function (array $match): string {
		$pad = $match[1];
		return $pad . "// Stalwart advertises QRESYNC. Horde waits for a CLOSED response\n"
			. $pad . "// the first SELECT does not send, so the folder is never cached and\n"
			. $pad . "// /apps/mail/api/messages stays HTTP 400.\n"
			. $pad . "\$params['capability_ignore'] = ['QRESYNC'];\n\n"
			. $match[0];
	},
	$source,
	1,
	$count
);
if ($count !== 1 || !is_string($updated) || !str_contains($updated, $marker)) {
	fwrite(STDERR, "mail-imap-compat: {$path} has no xoauth2 hook; Mail changed and the patch needs an update\n");
	exit(5);
}
if (file_put_contents($path, $updated) === false) {
	fwrite(STDERR, "mail-imap-compat: cannot write {$path}\n");
	exit(6);
}
if (function_exists('opcache_invalidate')) {
	opcache_invalidate($path, true);
}
echo "patched\n";
