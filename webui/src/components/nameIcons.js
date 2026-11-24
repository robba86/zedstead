// The icon a DayZ class name asks for by its own words: Land_Wreck_Caravan_BrownWood
// is a caravan, Barrel_Green a barrel, SeaChest a chest. One table for every
// place that draws a glyph for a name: item rows (ItemIcon.jsx), the Loot
// finder's buildings (BuildingIcon.jsx), the map's loot badges, spawn and
// trader markers (pages/mapeditor/icons/iconRules.js).
//
// The glyphs are Game-icons (game-icons.net, CC BY 3.0) through react-icons/gi:
// webui/licenses/game-icons.txt credits each author, and a glyph added here
// needs its line there.

import {
  Gi3dHammer, GiAirplane, GiBalaclava, GiBandageRoll, GiBandana, GiBanana,
  GiBarbedWire, GiBarn, GiBarracksTent, GiBarrel, GiBatteryPack, GiBearFace, GiBearHead,
  GiBellPepper, GiBelt, GiBlood, GiBoar, GiBowlOfRice, GiBread, GiBriefcase, GiBrodieHelmet,
  GiBunker, GiBus, GiCampingTent, GiCandleFlame, GiCannedFish, GiCaravan, GiCarBattery,
  GiCarDoor, GiCargoCrate, GiCarWheel, GiCastle, GiCastleRuins, GiCheeseWedge, GiChest,
  GiChicken, GiChipsBag, GiChocolateBar, GiChurch, GiCityCar, GiCompass, GiControlTower,
  GiCookingPot, GiCow, GiCowboyHolster, GiCrowbar, GiDam, GiDeer, GiDress, GiFactory,
  GiFarmTractor, GiFireAxe, GiFirstAidKit, GiFishCooked, GiFishingBoat, GiFishingPole,
  GiFlashGrenade, GiFlashlight, GiFlyingFlag, GiFountain, GiFountainPen, GiFox, GiFullMotorcycleHelmet,
  GiGardeningShears, GiGasMask, GiGasPump, GiGasStove, GiGoat, GiGreenhouse, GiHandSaw,
  GiHatchet, GiHelicopter, GiHoneyJar, GiHoodie, GiHospital, GiHouse, GiHut, GiJeep,
  GiJerrycan, GiKevlarVest, GiKiwiFruit, GiLabCoat, GiLandMine, GiLantern, GiLighthouse,
  GiLog, GiMachete, GiMechanicGarage, GiMedicalThermometer, GiMedicinePills, GiMegaphone,
  GiMetalPlate, GiMilkCarton, GiMining, GiMiningHelmet, GiMonclerJacket, GiMonkeyWrench,
  GiNails, GiOrange, GiPaper, GiPeach, GiPear, GiPig, GiPincers, GiPitchfork, GiPlanks,
  GiPocketRadio, GiPocketWatch, GiPoliceCar, GiPotato, GiPowerGenerator, GiPumpkin, GiRabbit,
  GiRawEgg, GiRolledCloth, GiRopeCoil, GiRoundSilo, GiSausage, GiScrewdriver, GiScythe,
  GiSewingNeedle, GiSheep, GiShipWreck, GiSickle, GiSkirt, GiSmokeBomb, GiSodaCan, GiSpade,
  GiSparkPlug, GiStable, GiSteak, GiSteamLocomotive, GiStonePile, GiSuitcase, GiTShirt,
  GiTomato, GiTorch, GiTreasureMap, GiTruck, GiWatchtower, GiWaterBottle,
  GiWaterMill, GiWell, GiWindmill, GiWireCoil, GiWolfHead, GiWolfTrap,
  GiBoxTrap, GiWoodAxe, GiWoodCabin, GiWoodenCrate, GiBacon,
} from 'react-icons/gi'
// Game-icons has no motorcycle: this one is Phosphor (MIT, webui/licenses/phosphor.txt).
import { PiMotorcycleFill } from 'react-icons/pi'

// [keys, glyph, needs?], first row with a hit wins, so a part (Hatchback_Door) sits
// above the vehicle it belongs to, and a compound (GasMask, BearTrap) above
// its words. A key is a whole word of the name, the word without its
// trailing number (Barracks2, sed01), or two words run together (SeaChest).
// A row with `needs` matches only when the name also holds one of those keys.
// The words that make an animal word mean the animal: its class, pelt or carcass.
const ANIMAL_CONTEXT = ['animal', 'dead', 'pelt', 'carcass', 'head', 'trophy', 'ambient', 'herd']

const NAME_ICONS = [
  // Compounds before the words inside them.
  [['policecar'], GiPoliceCar],
  [['gasmask'], GiGasMask],
  [['beartrap'], GiWolfTrap],
  [['teddy', 'teddybear'], GiBearFace],
  [['landmine'], GiLandMine],
  [['flashbang'], GiFlashGrenade],
  [['smokegrenade', 'smoke'], GiSmokeBomb],
  [['firstaid', 'firstaidkit'], GiFirstAidKit],
  [['labcoat'], GiLabCoat],
  [['motohelmet', 'motorcyclehelmet'], GiFullMotorcycleHelmet],
  [['constructionhelmet'], GiMiningHelmet],
  [['firefighteraxe'], GiFireAxe],
  [['carbattery', 'truckbattery'], GiCarBattery],
  [['sparkplug', 'glowplug'], GiSparkPlug],
  [['fishingrod'], GiFishingPole],
  [['firestation'], GiFireAxe],
  [['powerstation', 'powergenerator', 'generator'], GiPowerGenerator],
  [['controltower', 'atc'], GiControlTower],
  [['watchtower', 'guardtower'], GiWatchtower],
  [['seachest', 'chest'], GiChest],

  // Vehicle parts, above the vehicle in their name.
  [['wheel', 'tire'], GiCarWheel],
  [['door', 'doors', 'trunk'], GiCarDoor],
  [['battery'], GiBatteryPack],

  // Containers and storage.
  [['caravan'], GiCaravan],
  [['barrel', 'barrels'], GiBarrel],
  [['crate', 'woodencrate'], GiWoodenCrate],
  [['container'], GiCargoCrate],
  [['suitcase'], GiSuitcase],
  [['briefcase'], GiBriefcase],
  [['jerrycan', 'canister'], GiJerrycan],
  [['canteen', 'waterbottle', 'bottle'], GiWaterBottle],
  [['soda', 'sodacan'], GiSodaCan],
  [['barracks'], GiBarracksTent],
  [['tent'], GiCampingTent],

  // Tools.
  [['hatchet'], GiHatchet],
  [['axe', 'woodaxe'], GiWoodAxe],
  [['shovel', 'fieldshovel', 'spade'], GiSpade],
  [['pickaxe'], GiMining],
  [['hammer', 'sledgehammer'], Gi3dHammer],
  [['wrench', 'pipewrench', 'lugwrench'], GiMonkeyWrench],
  [['pliers'], GiPincers],
  [['screwdriver'], GiScrewdriver],
  [['saw', 'hacksaw', 'handsaw'], GiHandSaw],
  [['crowbar'], GiCrowbar],
  [['machete'], GiMachete],
  [['sickle'], GiSickle],
  [['scythe'], GiScythe],
  [['pitchfork'], GiPitchfork],
  [['shears'], GiGardeningShears],
  [['sewing', 'sewingkit', 'needle'], GiSewingNeedle],
  [['flashlight'], GiFlashlight],
  [['torch', 'headtorch'], GiTorch],
  [['lantern'], GiLantern],
  [['candle'], GiCandleFlame],
  [['compass'], GiCompass],
  [['map'], GiTreasureMap],
  [['radio', 'walkietalkie'], GiPocketRadio],
  [['megaphone'], GiMegaphone],
  [['flag', 'flags'], GiFlyingFlag],
  [['rope'], GiRopeCoil],
  [['barbedwire', 'barbed'], GiBarbedWire],
  [['wire', 'metalwire'], GiWireCoil],
  [['nail', 'nails'], GiNails],
  [['plank', 'planks'], GiPlanks],
  [['metalplate'], GiMetalPlate],
  [['log', 'woodenlog'], GiLog],
  [['stone', 'stones', 'rock'], GiStonePile],
  [['trap', 'snare'], GiBoxTrap],
  [['stove'], GiGasStove],
  [['pot', 'cookingpot'], GiCookingPot],
  [['paper'], GiPaper],
  [['pen'], GiFountainPen],
  [['pocketwatch'], GiPocketWatch],

  // Clothing.
  [['platecarrier', 'ballisticvest', 'vest'], GiKevlarVest],
  [['jacket', 'coat', 'parka', 'raincoat'], GiMonclerJacket],
  [['hoodie'], GiHoodie],
  [['tshirt', 'shirt'], GiTShirt],
  [['dress'], GiDress],
  [['skirt'], GiSkirt],
  [['helmet'], GiBrodieHelmet],
  [['bandana'], GiBandana],
  [['balaclava'], GiBalaclava],
  [['belt'], GiBelt],
  [['holster'], GiCowboyHolster],

  // Medical.
  [['bandage'], GiBandageRoll],
  [['rag', 'rags'], GiRolledCloth],
  [['tablets', 'pills', 'antibiotics'], GiMedicinePills],
  [['blood', 'bloodbag'], GiBlood],
  [['thermometer'], GiMedicalThermometer],

  // Food, the dish before the animal it came from (ChickenBreastMeat).
  [['sardines', 'tuna'], GiCannedFish],
  [['fish', 'carp', 'mackerel', 'trout', 'fillet'], GiFishCooked],
  [['meat', 'steak'], GiSteak],
  [['bacon'], GiBacon],
  [['sausage'], GiSausage],
  [['pear'], GiPear],
  [['banana'], GiBanana],
  [['orange'], GiOrange],
  [['kiwi'], GiKiwiFruit],
  [['tomato'], GiTomato],
  [['pepper'], GiBellPepper],
  [['potato'], GiPotato],
  [['pumpkin'], GiPumpkin],
  [['peach', 'peaches'], GiPeach],
  [['rice'], GiBowlOfRice],
  [['chocolate'], GiChocolateBar],
  [['chips', 'crackers'], GiChipsBag],
  [['milk', 'powderedmilk'], GiMilkCarton],
  [['egg', 'eggs'], GiRawEgg],
  [['honey'], GiHoneyJar],
  [['cheese'], GiCheeseWedge],
  [['bread'], GiBread],

  // Animals, by English word or the Latin name of the class (Animal_CervusElaphus),
  // only beside an animal word (ANIMAL_CONTEXT): a mod's Fox_Knife is a knife.
  [['deer', 'cervus', 'capreolus'], GiDeer, ANIMAL_CONTEXT],
  [['susdomesticus', 'pig'], GiPig, ANIMAL_CONTEXT],
  [['boar', 'scrofa'], GiBoar, ANIMAL_CONTEXT],
  [['cow', 'bos'], GiCow, ANIMAL_CONTEXT],
  [['sheep', 'ovis'], GiSheep, ANIMAL_CONTEXT],
  [['goat', 'capra'], GiGoat, ANIMAL_CONTEXT],
  [['wolf', 'canis'], GiWolfHead, ANIMAL_CONTEXT],
  [['bear', 'ursus'], GiBearHead, ANIMAL_CONTEXT],
  [['fox', 'vulpes'], GiFox, ANIMAL_CONTEXT],
  [['hare', 'rabbit', 'lepus'], GiRabbit, ANIMAL_CONTEXT],
  [['chicken', 'hen', 'gallus'], GiChicken, ANIMAL_CONTEXT],

  // Vehicles and wrecks.
  [['tractor'], GiFarmTractor],
  [['ikarus', 'bus'], GiBus],
  [['v3s', 'ural', 'truck'], GiTruck],
  [['uaz', 'offroad', 'jeep'], GiJeep],
  [['motorbike', 'motorcycle'], PiMotorcycleFill],
  [['sed', 'sedan', 'hb', 'hatchback', 'volha', 'lada', 'olga', 'sarka', 's1023', 'gunter', 'car'], GiCityCar],
  [['train', 'locomotive', 'wagon'], GiSteamLocomotive],
  [['ship'], GiShipWreck],
  [['boat'], GiFishingBoat],
  [['heli', 'helicopter', 'mi8', 'uh1y'], GiHelicopter],
  [['airplane', 'plane', 'aircraft'], GiAirplane],

  // Buildings.
  [['church', 'chapel', 'cathedral'], GiChurch],
  [['castle'], GiCastle],
  [['ruin', 'ruins'], GiCastleRuins],
  [['lighthouse'], GiLighthouse],
  [['hospital'], GiHospital],
  [['garage'], GiMechanicGarage],
  [['factory'], GiFactory],
  [['barn'], GiBarn],
  [['stable'], GiStable],
  [['silo'], GiRoundSilo],
  [['greenhouse'], GiGreenhouse],
  [['windmill'], GiWindmill],
  [['mill'], GiWaterMill],
  [['well', 'pump', 'wellpump'], GiWell],
  [['fuel', 'fuelstation', 'gas', 'gasstation'], GiGasPump],
  [['bunker'], GiBunker],
  [['dam'], GiDam],
  [['fountain'], GiFountain],
  [['shed', 'cabin'], GiWoodCabin],
  [['hut'], GiHut],
  [['house'], GiHouse],
  [['tower'], GiWatchtower],

  // A wreck with no known vehicle in its name.
  [['wreck'], GiCarWheel],
]

/** Every word key of a class name: whole words, words without their number,
 * and each pair of neighbours run together. */
export function nameKeys(name) {
  const words = String(name || '')
    .replace(/([a-z])([A-Z])/g, '$1 $2')
    .replace(/([A-Z]+)([A-Z][a-z])/g, '$1 $2')
    .replace(/([0-9])([A-Z])/g, '$1 $2')
    .toLowerCase()
    .split(/[^a-z0-9]+/)
    .filter(Boolean)
  const keys = new Set()
  words.forEach((w, i) => {
    keys.add(w)
    const bare = w.replace(/[0-9].*$/, '')
    if (bare) keys.add(bare)
    if (i > 0) {
      const pair = words[i - 1] + w
      keys.add(pair)
      keys.add(pair.replace(/[0-9].*$/, ''))
    }
  })
  return keys
}

// A colour that ends a name of two or more parts is the item's colour, not the
// thing it is: GunCase_Orange is an orange gun case (its swatch says so), while
// the vanilla food Orange is the fruit. Only the colours that are also a key
// above need to be here.
const COLOUR_SUFFIX = /_(orange)$/i

const cache = new Map()

/** The glyph a class name's own words ask for, or null when no word is known. */
export function nameIcon(name) {
  if (!name) return null
  if (!cache.has(name)) {
    const keys = nameKeys(name.replace(COLOUR_SUFFIX, ''))
    const has = (list) => list.some((w) => keys.has(w))
    const hit = NAME_ICONS.find(([words, , needs]) => has(words) && (!needs || has(needs)))
    cache.set(name, hit ? hit[1] : null)
  }
  return cache.get(name)
}
