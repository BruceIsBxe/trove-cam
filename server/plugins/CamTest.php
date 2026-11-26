<?php

/**
 * @name CamTest
 * @main CamTest\Main
 * @version 0.1.0
 * @api 5.0.0
 * @description trove-cam feasibility scene: places the camera player and two sparring NPCs in front of it.
 */

declare(strict_types=1);

namespace CamTest;

use pocketmine\block\VanillaBlocks;
use pocketmine\entity\animation\ArmSwingAnimation;
use pocketmine\entity\animation\HurtAnimation;
use pocketmine\entity\Human;
use pocketmine\entity\Location;
use pocketmine\entity\NeverSavedWithChunkEntity;
use pocketmine\entity\Skin;
use pocketmine\event\entity\EntityDamageEvent;
use pocketmine\event\Listener;
use pocketmine\event\player\PlayerJoinEvent;
use pocketmine\event\player\PlayerQuitEvent;
use pocketmine\item\VanillaItems;
use pocketmine\math\Vector3;
use pocketmine\player\GameMode;
use pocketmine\player\Player;
use pocketmine\plugin\PluginBase;
use pocketmine\scheduler\ClosureTask;
use pocketmine\scheduler\TaskHandler;
use function cos;
use function sin;
use function sqrt;
use function strlen;
use function chr;
use function ord;
use function min;
use function str_repeat;

/** A Human NPC that circles its partner, faces it, swings and knocks it back: something to film. */
class Fighter extends Human implements NeverSavedWithChunkEntity{
	public ?Fighter $foe = null;
	public Vector3 $centre;
	public float $phase = 0.0;
	public int $hitEvery = 9;
	public int $hitOffset = 0;
	private int $age = 0;

	public function onUpdate(int $currentTick) : bool{
		if($this->closed){
			return false;
		}
		$this->age++;
		$t = $this->age;
		$angle = $this->phase + $t * 0.035;
		$radius = 1.35 + 0.45 * sin($t * 0.11 + $this->phase);
		$tx = $this->centre->x + cos($angle) * $radius;
		$tz = $this->centre->z + sin($angle) * $radius;
		$dx = $tx - $this->location->x;
		$dz = $tz - $this->location->z;
		$len = sqrt($dx * $dx + $dz * $dz);
		if($len > 0.3){
			$dx *= 0.3 / $len;
			$dz *= 0.3 / $len;
		}
		$this->move($dx, 0.0, $dz);
		if($this->onGround && ($t + (int) ($this->phase * 10)) % 53 === 0){
			$this->setMotion(new Vector3(0, 0.42, 0));
		}
		$foe = $this->foe;
		if($foe !== null && !$foe->isClosed()){
			$this->lookAt($foe->getEyePos());
			if($t % $this->hitEvery === $this->hitOffset){
				$this->broadcastAnimation(new ArmSwingAnimation($this));
				$foe->broadcastAnimation(new HurtAnimation($foe));
				$push = $foe->getPosition()->subtractVector($this->getPosition());
				$push = new Vector3($push->x, 0, $push->z);
				if($push->lengthSquared() > 0.0001){
					$push = $push->normalize()->multiply(0.32);
					$foe->setMotion(new Vector3($push->x, 0.25, $push->z));
				}
			}
		}
		parent::onUpdate($currentTick);
		return true;
	}
}

class Main extends PluginBase implements Listener{
	private bool $sceneBuilt = false;
	/** @var Fighter[] */
	private array $fighters = [];
	/** @var TaskHandler[] */
	private array $swingTasks = [];

	protected function onEnable() : void{
		$this->getServer()->getPluginManager()->registerEvents($this, $this);
		$world = $this->getServer()->getWorldManager()->getDefaultWorld();
		if($world !== null){
			$world->setTime(6000);
			$world->stopTime();
		}
		$this->getLogger()->info("CAMTEST enabled");
	}

	public function onJoin(PlayerJoinEvent $event) : void{
		$player = $event->getPlayer();
		$info = $player->getPlayerInfo();
		$this->getLogger()->info("CAMTEST join name=" . $player->getName() . " skin=" . strlen($player->getSkin()->getSkinData()) . "B geometry=" . $player->getSkin()->getGeometryName() . " uuid=" . $info->getUuid()->toString());
		$this->getScheduler()->scheduleDelayedTask(new ClosureTask(function() use ($player) : void{
			$this->place($player);
		}), 40);
	}

	public function onQuit(PlayerQuitEvent $event) : void{
		$name = $event->getPlayer()->getName();
		$this->getLogger()->info("CAMTEST quit name=" . $name);
		if(isset($this->swingTasks[$name])){
			$this->swingTasks[$name]->cancel();
			unset($this->swingTasks[$name]);
		}
	}

	public function onDamage(EntityDamageEvent $event) : void{
		$entity = $event->getEntity();
		if($entity instanceof Fighter || $entity instanceof Player){
			$event->cancel();
		}
	}

	private function place(Player $player) : void{
		if(!$player->isOnline()){
			return;
		}
		$world = $player->getWorld();
		$spawn = $world->getSafeSpawn();
		$base = new Vector3($spawn->getFloorX() + 0.5, $spawn->getFloorY(), $spawn->getFloorZ() + 0.5);
		if($player->getName() === "TroveCam"){
			$player->setGamemode(GameMode::CREATIVE);
			// yaw 0 faces +Z (south); a slight downward pitch frames the fighters
			$player->teleport($base, 0.0, 10.0);
			$this->buildScene($player, $base);
			$this->getLogger()->info("CAMTEST camera placed at " . $base->x . "," . $base->y . "," . $base->z);
			$this->getLogger()->info("CAMTEST ready");
		}else{
			$player->teleport($base->add(2.5, 0, 8.0), 180.0, 0.0);
			$player->getInventory()->setItemInHand(VanillaItems::IRON_SWORD());
			$this->swingTasks[$player->getName()] = $this->getScheduler()->scheduleRepeatingTask(new ClosureTask(function() use ($player) : void{
				if($player->isOnline()){
					$player->broadcastAnimation(new ArmSwingAnimation($player));
				}
			}), 14);
			$this->getLogger()->info("CAMTEST bot placed name=" . $player->getName());
		}
	}

	private function buildScene(Player $camera, Vector3 $base) : void{
		if($this->sceneBuilt){
			return;
		}
		$this->sceneBuilt = true;
		$world = $camera->getWorld();
		$x0 = (int) $base->x;
		$y0 = (int) $base->y;
		$z0 = (int) $base->z;
		// a backdrop wall of varied blocks so texture, lighting and depth are easy to judge
		$palette = [
			VanillaBlocks::STONE_BRICKS(), VanillaBlocks::OAK_LOG(), VanillaBlocks::BRICKS(), VanillaBlocks::GLASS(),
			VanillaBlocks::BOOKSHELF(), VanillaBlocks::GOLD(), VanillaBlocks::DIAMOND(), VanillaBlocks::TNT(),
			VanillaBlocks::EMERALD(), VanillaBlocks::GLOWSTONE(), VanillaBlocks::SEA_LANTERN(),
		];
		$n = count($palette);
		for($dx = -8; $dx <= 8; $dx++){
			for($dy = 0; $dy < 3; $dy++){
				$block = $palette[($dx + 8 + $dy * 3) % $n];
				$world->setBlockAt($x0 + $dx, $y0 + $dy, $z0 + 11, $block);
			}
		}
		$skin = $camera->getSkin();
		$centre = new Vector3($base->x, $base->y, $base->z + 4.5);
		$red = $this->makeFighter($world, $centre->add(-1.3, 0, 0), self::tinted($skin, "camtest_red", 330, 170, 170), "Red", $centre, 0.0, 0);
		$blue = $this->makeFighter($world, $centre->add(1.3, 0, 0), self::tinted($skin, "camtest_blue", 170, 190, 340), "Blue", $centre, 3.14159, 4);
		$red->foe = $blue;
		$blue->foe = $red;
		$red->getInventory()->setItemInHand(VanillaItems::DIAMOND_SWORD());
		$blue->getInventory()->setItemInHand(VanillaItems::IRON_SWORD());
		$red->getArmorInventory()->setHelmet(VanillaItems::DIAMOND_HELMET());
		$red->getArmorInventory()->setChestplate(VanillaItems::DIAMOND_CHESTPLATE());
		$blue->getArmorInventory()->setHelmet(VanillaItems::IRON_HELMET());
		$blue->getArmorInventory()->setChestplate(VanillaItems::IRON_CHESTPLATE());
		$blue->getArmorInventory()->setBoots(VanillaItems::IRON_BOOTS());
		$this->fighters = [$red, $blue];
		$this->getLogger()->info("CAMTEST fighters spawned");
	}

	private function makeFighter(\pocketmine\world\World $world, Vector3 $pos, Skin $skin, string $name, Vector3 $centre, float $phase, int $hitOffset) : Fighter{
		$fighter = new Fighter(Location::fromObject($pos, $world, 0.0, 0.0), $skin);
		$fighter->centre = $centre;
		$fighter->phase = $phase;
		$fighter->hitOffset = $hitOffset;
		$fighter->setCanSaveWithChunk(false);
		$fighter->setNameTag($name);
		$fighter->setNameTagAlwaysVisible(true);
		$fighter->spawnToAll();
		return $fighter;
	}

	/** The camera's own skin with a colour cast, so the two fighters are easy to tell apart. */
	private static function tinted(Skin $skin, string $id, int $r, int $g, int $b) : Skin{
		$data = $skin->getSkinData();
		$len = strlen($data);
		if($len === 0 || $len % 4 !== 0){
			$data = str_repeat("\x80\x80\x80\xff", 64 * 64);
			$len = strlen($data);
		}
		$out = "";
		for($i = 0; $i < $len; $i += 4){
			$out .= chr(min(255, (ord($data[$i]) * $r) >> 8));
			$out .= chr(min(255, (ord($data[$i + 1]) * $g) >> 8));
			$out .= chr(min(255, (ord($data[$i + 2]) * $b) >> 8));
			$out .= $data[$i + 3];
		}
		return new Skin($id, $out, "", $skin->getGeometryName(), $skin->getGeometryData());
	}
}
