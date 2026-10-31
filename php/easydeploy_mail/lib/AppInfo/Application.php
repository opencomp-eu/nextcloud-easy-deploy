<?php

declare(strict_types=1);

namespace OCA\EasyDeployMail\AppInfo;

use OCA\EasyDeployMail\Listener\ProvisionMailPassword;
use OCP\AppFramework\App;
use OCP\AppFramework\Bootstrap\IBootContext;
use OCP\AppFramework\Bootstrap\IBootstrap;
use OCP\AppFramework\Bootstrap\IRegistrationContext;
use OCP\User\Events\UserLoggedInEvent;

class Application extends App implements IBootstrap {
	public const APP_ID = 'easydeploy_mail';

	public function __construct(array $urlParams = []) {
		parent::__construct(self::APP_ID, $urlParams);
	}

	public function register(IRegistrationContext $context): void {
		$context->registerEventListener(UserLoggedInEvent::class, ProvisionMailPassword::class);
	}

	public function boot(IBootContext $context): void {
	}
}
