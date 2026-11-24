"""Default seed data for new server setups."""

from __future__ import annotations


# Note `WORKSHOP_MOD_IDS` is for:
# 1. Not having to go to steam.com and get the workshop id
# 2. Setting the order of the `-mod=` attribute when starting server
#
# Position is a load-order rule where two mods do not depend on each other.
# @ToxicZone sits above every Expansion module on purpose: its MissionServer
# replaces CreateCharacter without calling the original, so loaded after
# Expansion it switches off Expansion's spawn selection (players spawn at
# random, with no picker). Loaded before, Expansion wraps it and the picker
# works; RadZone, loaded last, does not interfere (plans/48 Step 0, measured
# by a join in 2026 Q3).
WORKSHOP_MOD_IDS = """\
@CF=1559212036
@Community-Online-Tools=1564026768
@VPPAdminTools=1828439124
@VPP Admin Comp Utility=3674717879
@Sobr Foolproof Admin=2930930547
@Admin Event Kits=3618611876
@Dabs Framework=2545327648
@ToxicZone=1752669393
@DayZ-Expansion-Core=2291785308
@DayZ-Expansion-Licensed=2116157322
@DayZ-Expansion-Animations=2793893086
@DayZ-Expansion-BaseBuilding=2792982513
@DayZ-Expansion-Map-Assets=2792983824
@DayZ-Expansion-Chat=2792982897
@DayZ-Expansion-Groups=2792983364
@DayZ-Expansion-Name-Tags=2576460232
@DayZ-Expansion-Navigation=2792984722
@DayZ-Expansion-Spawn-Selection=2804241648
@DayZ-Expansion-Quests=2828486817
@DayZ-Expansion-Missions=2792984177
@DayZ-Expansion-Market=2572328470
@DayZ-Expansion-AI=2792982069
@DayZ-Expansion-Hardline=2828487396
@DayZ-Expansion-Vehicles=2291785437
@DayZ-Expansion-Weapons=2792985069
@DayZ-Expansion-Book=2572324799
@DayZ-Expansion=2116151222
@DayZ-Dynamic-AI-Addon=2874589934
@Bitterroot=2906823750
@DeerIsle=1602372402
@Namalsk Island=2289456201
@Namalsk Survival=2289461232
@Esseker=2462896799
@TakistanPlus=2563233742
@Banov=2415195639
@Valning Map=1880753439
@TraderPlus=2458896948
@RadZone=2294323732
@HypeTrain=3115714092
@HypeTrain Namalsk=3116103277
@IRP-Land-Rover-Defender-110=1912237302
@CarVanillaPlus_v2=2780278027
@Ear-Plugs=1819514788
@PlayerStatsHud=2435218872
@TheDoctor Retro Gamer Collectables=3049835903
@XZone STALKER Mutants=2525642572
@zombie_horde=2636715684
@Central European Asset Pack=2330497955
@Stargate Teleporters Complete=2933015619
@Ambient Animals Pack=3114410963
@JVDS Fish=2829486979
@Laser Sight=3082196500
@MMG - Mightys Military Gear=2663169692
@Realistic Suppressors=3082801898
@SFE-Special Force Weapon=3050765647
@SNAFU Weapons=2443122116
@FOX WEAPONS 2.0=2723807644
@126 Main Menu Music Fix RELIFE=3256040393
@128 Meru_Car_Pack=3493623721
@2003 Mitsubishi Eclipse Spyder FREE=3620623098
@3D Printer=3363783478
@4KBOSSKVehicles=3369325490
@A-10 Warthod Mod=3691397873
@A-Frame-House=3414548267
@Abandoned Hospital=3658225595
@Acces Inventory In Car=3238530433
@ActionTeleport=3405070168
@Admirals Advanced Hologram Mod=3690267695
@Advanced  Player Death Spawn=3504928478
@Advanced Landmines amp Minefields=3593223672
@Advanced Medicines and Items=3034460305
@AI Bandit Voices=3679500367
@AI Bandits=3628006769
@AI War Zones=3521304609
@AirBallonEvents=3674017992
@Airborne AI=3472298147
@Airdrop-Upgraded=1870524790
@AJs Creatures V2=3413364741
@AJs Weapons=3571685323
@AK-12-RUS=3630429035
@Alevarics Clothing Overhaul=3354931239
@Alexandria Currency=3691467322
@Alien Invasion=2840597308
@All Dayz Gone=3691070294
@Alpha_Wolf=3604879301
@Ammo Crate RELIFE UPDATED=3437619605
@Ammo Crate RELIFE=2967939449
@AmmoStackBullet=2832884779
@Ancient_ruin=3415279352
@AnimalPeltResize=3444532892
@Animated NVG Toggle=3465327854
@AnimatedDynamicHelicopters=3382463948
@Antoria=2966495799
@Area Of Decay Building=2871588282
@Arma Bicycles=2971993210
@ArmA2 Trucks=3593276829
@ArmorPlates=3589337216
@Armour construction antirade=3236674826
@ARSENICS C10 Chevrolet=3355356253
@Artefact Redux FREE=3168384897
@Asmond Vanilla Weapons=3640419203
@AuraInventory=3652345718
@Auto Close Containers=3564836300
@Auto Outfit=3688552932
@Auto Run Unlimited Stamina=3457276679
@Auto Stack - Proper=3639591480
@AutoCarCover=3489483195
@AutoCarFlip=3422227460
@AutolockVehicles=3167286994
@Avalon=3225634444
@Aventador_LP700=3426285116
@Azrael Dice Game=3409128280
@Azraels Horrible Objects free=3571602199
@Azraels Skeletons for DEditor=3071108413
@Back to the Future DMC12=3660629354
@Backpack Wings=3565245052
@BackpackInHands=3566118912
@BallerZ Garage=3646080855
@BallerZ Gear=3025712002
@BallerZ Kronic Heli Retextures=3030813297
@BallerZ Upgradeable Tactical Gear=3638122157
@Ballistic Shield Riot Shield=3237813519
@BarrelCarrying=3258735627
@BaseBuildingPlus=1710977250
@bastard_maz=3447015977
@BattlePass - Premium Version=3691052725
@Bbque Mag Loader Workbench=3443459638
@BCK_Assets=3691927752
@BCK_Core=3691982209
@BCK_HM=3691956067
@BCK_TB=3691944207
@BCK_YRTSK=3691967398
@Bers Mutant Creature=3437138388
@Better Batteries=3146753440
@Better_Humvee=3525927566
@BetterBoltAction=3641964151
@BetterDriving=3651248193
@BetterTrac=3191155841
@BeyondBounds=3576361539
@BiathlonTarget=3690040452
@Big Grampa - Tree House=2635249250
@BillboardZ=3073465081
@Bitcoin Mining FREE=3485361778
@Blackouts Scorpion=2909935986
@Blacks BBP Interior=2957760073
@Blacks MMG Retex=2957747852
@Block Building=3652964249
@BLR Portable Houses Upgraded=3673762621
@BLR Vehicle Insurance=3691265366
@BM VAZ 2107=3377632060
@BMW_M2_F22=3437980559
@BMW_M4_G82=3437951425
@BMW_M5_CS=3440100806
@Boat Kit Mod  Expansion Boat Addon=3512265560
@Bodybag Recovery Cone=3623609592
@Boomlays bed sleep fix=3690144329
@BoomLays Things=2860643107
@BotGroupRewards=3687760846
@Bounty Hunter=2842902704
@BreachingCharge_Remastered=2959987085
@Brok Mutant Creature=3451940343
@Brutalist Building=3514809506
@BS HackedCrate=3482229348
@BS KeyRoom=3514469093
@Bubaku Doors=3676586330
@Bubble Head Nurse=3355231466
@Buddys Puzzles=3047115033
@Buddys ZombieDog=3026707489
@BuddysCapes=3118751741
@BuddysPack=2935743437
@BuddysZombies=3037004188
@Building Backpack=3668253997
@BuildingHealth=2832891659
@Buildings_from_Mihalych_road_big=3434244261
@Buildings_from_Mihalych=3270425957
@BuildStack X2=3011221120
@BuildStack X3=3011222088
@Bullet Stacking - More Ammo=2951379570
@Bunker Assets=3476021582
@Bunker Solnechniy=3101930408
@Bunker Storozh Cherno=3351827792
@Bunker_Сhernarus=3351724289
@Burn Campfire Anywhere=3429751433
@Bushcraft=3237568178
@Butler Creek Magazine Loader=3653037515
@ButtTube=3174623596
@BW_Baretts=1641886492
@BXD_Animals=3679548242
@BXD_Cadeado=3683969807
@BXD_Combine=3493114938
@BXD_Zombie=3486002207
@BZE Structures=3649999608
@CableReel_me777=3690127678
@Cadillac CT4V FREE=3563551478
@Camping Chairs=3535722402
@Can Bury Barrel=3429789396
@Canister Gasoline slot in car=3639321893
@Canning_LonerMod=3235884191
@Capture Territory=3465134065
@Car Crash=3491235037
@Car PDA=3677698666
@Car Showroom=3444549906
@CarDamageConfig=3353822981
@CardboardBox=3469208984
@CargoSorting=3276386418
@Carnage Pail Kids collectable cards=3559843427
@CarPack=3443311258
@cars_pack_by_bks=3582411149
@CashRobbery=3479703031
@Cassette Slots for CZ Autoz DayZ Mods=3417390687
@CCBYAssets_wrecks=3439866266
@Charger_RT=3430566657
@Chemical Protection with JSON RELIFE=2870351844
@Chernarus - Echoes of Gorka=3373263307
@Chernarus2035=3638582966
@CHILLERS Cherno=2895289038
@ChinazesMOD=3692221108
@CHWpack=3690617426
@Clean amp Build=3528925483
@ClothingSlotsFix 125=3348732195
@CnGUAZ_31514=3160244059
@CnGUAZ_452=3359682865
@CnGVAZ_2109=3593929500
@Cocaine Bear=3192475423
@Code Lockable Starter Shed=3636106243
@Collectables Plus=3362376100
@Collections=3264602090
@Combined Repair Tools=3429544684
@Compatible Picatinny Rail Optics=3425958023
@Container Cabin Blue=3436274364
@Container Cabins=3245992656
@ContaminatedEffect=3390406838
@Convoy Military Event=3465309989
@CORGI_corgi=3568740233
@Corrosion_Wonderer=3498093683
@CorvaxOutbreak=3688831466
@Cottage with Fireplace=3253678483
@Craft_Preview_Pro=3287409694
@CRAFTING FOOD=2874029657
@Crafting Repair Tools=3176951485
@CraftSystem=3626433444
@Crashed Plane Asset=3119592638
@Crate System by SobrMods=3466757800
@creepysoundsserver=3611991381
@Crocodile=3013430583
@crossbag=3325550641
@CS_Mutant_DayZ=3131461361
@Custom icons=3453697178
@Custom Keycards=2810212624
@CustomCraft Work In Progress=3441612325
@CW_Gamelabs=3548025008
@CZ Optics=3571068454
@CZLRoadCone Fix - Manually Activated by Player=3160380491
@DAG_AnimalSkinning=3371242112
@DAG_Particle=3689811578
@DAG_ZombiSkinning=3375569942
@Damage System RELIFE=2989237330
@DamageAndBloodBar=3278038253
@DarkMap=3581716261
@DarkMoonServer_4assets=3691483287
@DayZ Camo Overhaul=2452096533
@DayZ Horse=3295021220
@DayZ Loaded Server Mod=2896586336
@DayZ Shark=3147619641
@DayZ-Bicycle=2971190303
@Dayz-Cat=2947349326
@DayZ-Dog=2471347750
@DayZ-Rabbit=2948562704
@DayZ-Rat=2950280649
@DayZ-Raven=2959526520
@DayZ-Seagull=2979737322
@DayZ-Snake=2956395819
@DayZ-Squirrel=2955489764
@DayZ-TukTuk=3248564210
@DayzFrance_Deathmatch=3692235491
@DayZlauncher=3692150409
@DayZTE  DayZ Types Editor=3692398188
@DBN_FordRaptor_Scorpio=2809757799
@DBR_LittleBird_RFFSHeli_Skins=3279783147
@Dead Earth Modpack=3690281485
@Dead Silence Transport 3 Season=3443170094
@Death Stranding Backpack=3245059166
@DeceasedCollectibles=3422527589
@DeerIsle Core=3690576926
@DeerIsleFix=3441580267
@DeliveryDrone=3358955263
@Delta Paintball=3447867663
@Deltas Ammunition Info UI=3491989895
@DemonCreatures=3134193051
@Dialog System RELIFE=3495472300
@Dirty Fresh Cabins=3332549686
@Disable Temp=3452610888
@Disable Wind=3195952329
@DISCORD_SHOP=3689616024
@DLC Retextures Pack Clothes Color Welliton=3105454319
@DLC Retextures Pack Clothes Welliton=2862333901
@DLC Retextures Pack Container Welliton=2965211482
@DLC Retextures Pack Weapon Color Welliton=3063600739
@Docs Car Fixes=2886222035
@dodge_rust_Z_radash=3048162966
@DodgeRam3500=3437312998
@Dog Meat=3296769665
@Dougies Event Auto Claim=3685183253
@Dougies KOTH Framework MZ_KOTH Addon=3688269809
@DrugTradersLab Deerisle=3689416534
@DudeWheresMyCar=3491661516
@Dumper Anrufen=3303744955
@DW Sleeping Mod  Advanced Spawn amp Resting System=3690569223
@DW_Safebox=3690728623
@Dying Light UV=3356131552
@Dynamic Scavenging=3682329748
@DynamicContaminatedAlert=3145048518
@DZR DayZ Bicycle Kit=3170306604
@DZR Vehicle Parts Degradation=2947630018
@Early Winter or late Fall in Chernarus=3303988563
@Early Winter or Late Fall Livonia=3346291656
@EAZbarrel=3452313820
@ElderWoodz Map Coming Soon as Public Release=2907860772
@Elegant Clothes=3633294773
@Empty Map=3044800950
@Enable Inventory In Vehicle=3594596641
@Evelone 2666=3678497998
@Event-Horde=3047539495
@ExpandedExpansionBuilding=3689648120
@ExpandedExpansionRaiding=3688395011
@Expansion Prototype spaceship=3456199351
@EXPANSION Silence of Cities PVE=2880983779
@EXPANSION Silence of Cities PvP=2880973840
@Expansion_ HuntingQuestsDx=3450741205
@ExpansionMoneyRewardSystem=3605381078
@Explosion Fragments=3441651115
@EXSoundZone=3684016422
@Extraction Point Server Map=3691459070
@FC_Fish_Equip=2937138060
@FC_Uaz_Pickup_Rest=3534186266
@FC_UAZ_Pickup=3056581501
@Fear the Darkness=3661691727
@FemaleHead=3402771132
@First Aid Kit on Belt=3645428906
@Fishy Log Cabin=3479375054
@FlashlightAutoMode=3663964056
@FlashlightPlus=3673543042
@Flickering _StreetLights=3486077541
@FLIP CAR=3623001011
@FlipTransport Hydraulic Jack Slot=3246857934
@FlipTransport=1832448183
@Floating Combat Text=3684509954
@Floating Toys=3671674479
@Forward Operator Gear=2931560672
@FoXyKOTH=3688828884
@FoXyRadio=3075855922
@Freddy Krueger=2865063040
@Free Cassette Recorder=2880919627
@Free Flying backpacks=3244276873
@Free LV backpack=3244311763
@Free Marching backpack=3245184831
@Free Mickey backpacks=3244898218
@Free Tech backpack=3244364900
@FREE WEAPON PACK 15 MODEL=3510965052
@Free_Gun_Tranquilizer_ByHare=3670662855
@Free_Pack_Knife=3582471969
@FREE_WHITELIST_DayZ=3684224577
@Free_Zombi_Pack=3547607117
@FridForsMods Shron  Tainiki Spawner=3494448363
@Fuel_serverMod=3245074285
@FuelControl=2834415801
@Fungi CZ Auto Cassette Slot=3418832183
@Fungi Shelf=3672455439
@GC Medicine Injector=3581166104
@Gerphelius_CarDamageControl=3651378401
@Gerphelius_CarXFence=3648615393
@GF STALKER Props=3683579321
@GF_Barrels=3023834511
@Ghillie Slots=3449266072
@Ghillie Sniper Free=3631298930
@GhillieFix=2889783501
@GhillieHoodFix=2893103893
@Ghost Mara=3581137792
@GiveampTake=2903112334
@GLITCH EXPRESS  MAIL-AUCTION  P2P=3592170970
@GlobalChatPlus=3471358849
@Glock18=3181027730
@GMC 1969 Pickup=3671953078
@Good Ole Dayz ARS Armor=3691699888
@Grakkul=3378851571
@Grow Tent=3683890416
@GUI Effects RELIFE=2962048201
@Halloween=2879465205
@Haralds Armory=3325200572
@Hare_Free_Mob=3546824382
@Hazmat suit Free=3628878721
@HB KeyRooms=3247080051
@heavymetalweapom=3690503143
@Helicrash Search Updated RELIFE=3121468488
@Hercul Mutant Creature=3428498624
@HEX BOT Discord=3552741822
@HexDayZ=3435287100
@HGC Leveling System=3446823299
@Hitmarker Updated=3514081165
@Honda_NSX=3433270818
@HornZ=3392320085
@Horse Expansion=3297432307
@HorseBag_Upgraded=3573022530
@Hospital Building=3502745090
@HP Crafter Workbench=3133007745
@Hunter Bow=3464752232
@HUNTER_CAREER__P2P=3686228182
@HuntingMod=3685890769
@IAT_Roleplay_Emotes=3438645799
@iM7s_DrugRunning=3465841846
@Improved Hunting Scope=3565888725
@Improved Weapon Lift=3422628224
@InediaInfectedAI=3031784065
@Infinite Ammo Reloading Case=3477769024
@INFO КПК=3687766381
@INVICTUZ_Barrel_Storage=3406014628
@InvisibleDynamicToxicFIX=3620439008
@ISTOK PVE=3107485892
@IZH 56 Motorcycle=3595218608
@JDs Animated Weapons=3297234994
@Jeezys Army Bags=3333407337
@Jenis_Dome=3536526800
@JosiesBroncoZ=3357830076
@JosiesChevyK20CrawlerZ=3426810213
@JosiesChevySilveradoZ=3429584366
@JosiesHaloWarthogZ=3412498833
@JosiesHiluxZ=3397387579
@JosiesJeepCherokeeZ=3660754716
@JosiesJeepZ=3235269377
@JosiesPropZ=2972968831
@JosiesPUBG_BuggyZ=3372912066
@JosiesRustyPickupZ=3394988924
@JosiesStepSideChevyZ=3352647423
@JosiesZombieZ=3077143744
@JP Extended Batteries=3663754523
@KENT FREE WEAPONS  SCI-FI=3495979156
@Kerberos Gear=3427266108
@KEY_CHAIN=3274789408
@KeyCard-Rooms Better=3332979792
@Knife Pack=2932234007
@KNIFE_BOOST=3691685179
@Knock Knock AI=3497685709
@Knock Knock Zombies=3499461862
@KoronaVirus Alicja Zombie=3461050703
@KoronaVirus HUD Icons=3464123692
@Krak3nsAmmoStacks100=3155026779
@Krasno Airfield Overhaul=3684405942
@KT PDA  Server_Information_Panel Layout=2828209716
@KZ_AutoRunToggle=3619201418
@Lamborghini Countach=3182442372
@Last Road RP Items Pack=3443508153
@Last Stand  Evac Marker=3475576559
@LastChanceRing=3618803822
@Left 4 Dead zombie sounds=3103202419
@LehsFix=3295781557
@Less Weapon Sway=3342066952
@Letter Board Signs=3637720560
@Lexus_LFA=3435362678
@LIGHTS=2822125184
@LKZ AutoCargo=3277782619
@LKZ Car pack=3528468414
@LMs Ferrari F40 Car Modular Vehicle=3476969365
@LMs Harrier MkII Jet - Vehicle=3477882911
@LMs Helicopter Flight Systems=3624740747
@LMs Patty Wagon Vehicle meme=3478920638
@LMs Planes=3639695989
@LMs The Homer=3505653126
@Lnr_ChargerBattery9V=3290156248
@LockPick Mini Game RELIFE=3295699793
@Loot Barrel Transfer=2832887415
@Loot_Spawn_BlocksV2=3495356491
@Lootrooms=3476361311
@LootZone=3517112997
@LostFiles Chernarus=3343140926
@LostFiles=3282433316
@M134 Minigun=3327751110
@M3SPro=3045504365
@M4 Customized  Attachments=3528769999
@M72-LAW=3300858444
@MA Container Base=3403240749
@MA Pirate Treasure=3440974241
@MA Props=3294174744
@MadTwoTest=3690096259
@MAGIC_BOX=3684217836
@MagLoaderBox=3164839000
@Mando Suit=3492022510
@Map Assets - MutatedMods=3077629858
@MapLink Hive=3478983598
@Mapping_Kamensk_Military=3415928833
@Mask Release=3189579837
@Material pack Free=3620127965
@Matrix Clothing Set=2917618116
@MB Raygun MKII=3597222670
@MBM_ApocalypseTruck=3661300360
@MBM_ApocalypticPAZ=3665166604
@MBM_ChevyImpala1967=3475246850
@MBM_GoKart=3456445155
@MBM_HarleyDavidsonFatBoy=3665840738
@MBM_SuzukiGSXR750=3453337558
@MBR-Loading=3691142241
@MeatCombiner=3404567862
@Megaloot DNA Wallet=2874191563
@Mercedes-Benz 12L 1948=3432884787
@Mercedes-Benz AMG G63 W463=3585192316
@Mercedes-Benz S-Type 1928=2855192839
@Mercedes-Benz S63 AMG FREE=3674457493
@Mercedes-Benz SL 65 AMG Black Series=3037633408
@Mercedes-Benz Yachting FREE=3590888407
@Meru Car Pack English Version=3511486256
@Meru_Car_Pack=3119849334
@Metro Watch=3133047673
@MH Anomaly=3689109769
@MHOTeleporter=3534207167
@MidnightPack=3277191886
@Military Objects Pack=3469320529
@Military Survival backpack=3245085617
@MJO Indominus Rex=3598539725
@MJO Prehistoric Fish Mod=3484267975
@MK18 Mjolnr=3619029347
@MMG - Louis Welliton=2838395334
@MMG - Welliton Camo Retexture=2834720758
@MMG - Welliton Red Rose Retextur=2841354574
@MMG - Welliton WhiteSticker Retexture=2833969461
@MMG Base Storage=3210162677
@MMG Inventory AccessControl=3596474953
@ModFactory - Bandage Animation=3584910395
@Modular Dungeon=3408840454
@Modular Foot Bridge=3244543961
@MordorTac GOP TAC ReTextures=3310204143
@More Durable Clothing=3431208074
@More Durable Weapons=3431213169
@More Injectors RELIFE=2914798191
@More Injectors Updated RELIFE=3119147162
@More Radio Frequencies=3692038037
@MoreFood V2=3534507229
@MoreMaterials=3149798901
@MorePresents=2312726441
@MoreSurvivors=2863326324
@Mortys Weapons=2155726353
@Mounts amp Sights=3322252091
@MPG_CarFellingTrees=3472997019
@MPG_CheckLifeTime=3206208143
@MPG_LootExtractor=3422882242
@MPG_Spawner=3120779051
@MPG_TraderPlus_RemoveBrokenReceipt=3484454711
@MSFZ Landrover Defender=3321062194
@MuchStuffPack_Fix 128=3492804519
@MuchStuffPack=1991570984
@Multi Room Building=3669251430
@Multi-Skin M4A4=3417066613
@Multiplix Territory Deprecated=3434410348
@Muranos CarLock System=3682800023
@Mutant Free=2911581830
@Mutant Statues=3610225361
@MutantEFX=3683483574
@MutantZombie Spawn System=3551827469
@My Armor Pack 1=3366496406
@My Backpacks=3397511811
@My CODM Vehicles ATV=3408335003
@My CODM Weapons=3390675689
@My DeadIsland2 Weapons=3384736719
@My Delta Force Gear=3427626164
@My Delta Force Medical=3441347183
@My Delta Force Weapons=3612546508
@Namalsk Village=3692180094
@NamantHs 1963 Beetle=3663509262
@NamantHs 1978 ElCamino=3491784674
@NamantHs 2003 Cadillac Escalade=3690376951
@NamantHs BoxTruck=3618408142
@NamantHs Buick Regal=3525853774
@NamantHs Mercedes 300SEL=3636006271
@NamantHs Mercedes 560SEL=3663777521
@NamantHs Nomad Wagon=3684971115
@NamantHs Rusforma Lights-Sirens=3337108005
@NamantHs Vehicle Pack=3663803192
@Native Tribes Indians=3368648581
@NatureOverhaul=2106663048
@Nautiqs Garage House=3588400917
@Nautiqs_Police_Station=3586245594
@NBC Case Plus=3297669060
@NBC Case=3449190162
@Nemesis T-Type=2958730420
@Nemsis Craftingpack All in One=3606014796
@New Wells RELIFE=2965360212
@NexusMod=3691756186
@NIGHT GHOST Clothes=3636584437
@Ninjins-Advanced-ItemStats=3632998823
@Ninjins-PvP-PvE=3381664818
@Nissan GTR FREE=3214402149
@No Branch Cracking=3435529086
@No fog For 126 and =3349492657
@No Sprinting Zombies=1816010715
@No vehicle damage by SobrMods=3202967830
@NokiaZ=3559959097
@NomNom Collectibles=3282237841
@North Takistan=3415898181
@Northwest Airfield  Chernarus mapping vanilla=3068200180
@NotificationsSystem=3624261153
@Novikostok=3553075906
@OFG Nuclear Zone=3608781425
@OMD_PowerLight=3203771893
@OMD_Radio=3410416232
@Online Discord Bot Monitoring=3688619571
@OPEN INVENTORY CAR=3066153200
@Openable Ammo Cans=2935903943
@OrigemZ Transport=3368578057
@PACCBET_PACK=3484591722
@Pack complete - Backpacks FREE=3648464156
@Paragon Arsenal=3047952845
@Paragon Gear and Armor=2820370970
@Paragon Storage=3010267444
@PARTY_MODs=3688851331
@Peek Doors=3647777793
@PercentageHUD=3443562573
@Perks amp Skills System Updated RELIFE=2930657404
@Pervak=2961325332
@PLA_VEHICLE_CSK131_Camo=3414065780
@PLA_VEHICLE_CSK131=3414057379
@PLA_Weapons_Pack=3300415500
@PlateCarrierFix=2892294760
@PlayableZombieSkins=3659105345
@Player Bounties=2845071886
@Player Logger=3497846712
@Plush Toys RELIFE=2914020533
@PMC Wagner ReTextures=2995981182
@PolaroidCollectibles=3360973067
@Poop System=3594032119
@PortableTraderSystem=3659699244
@Predator mask=2851634250
@PripyatGamma=3136720512
@Pristine Repair=3189514422
@Prizs Weapon Mod=3148403789
@Prohibition of suicide=3691904188
@ProjectHope Survivor Backpack=2926289548
@Promocodes v2 FREE REPACK=3667436075
@ProTraction=3656437172
@PsevdoGigant=3659823139
@PSY_Emission=3593402107
@Psyerns Radio Show=3627848296
@Psyerns Sound System=3623510671
@Psyerns Teleport Overhaul=3689057982
@PVE SYSTEM=3304780902
@PVE_System=3547431214
@PVP Movement=3434610287
@QRCodeGenerator=3691221129
@QuickMoveItemsByCategory=3332001143
@R22R_Base_Fortifications_RELOADED=3389527449
@Radiation=3570307512
@Radio by Bourbon=3324786374
@RADIO STATION=3556560781
@Radio_Fungi_Car_Radio_Addon=3405725509
@Radioactive Animals=2485015982
@RaG_BaseBuilding=3157695626
@RaG_BaseItems=2878980498
@RaG_Dragons=3310715247
@RaG_Evil_Snowman=2886730223
@RaG_Halloween=3045497789
@RaG_Immersive_Wells=3406196859
@RaG_Vehicle_Pack=2880254695
@RaG_Wheel_Of_Fortune=3308989311
@Raman=3401182744
@Random Ignite Chance=3634658489
@RandomItemsPack=2933630645
@Razor Falcon Free=3638107818
@RDL_RealDrive V10=3612945607
@Real-World Ammo amp Ballistics=3491970285
@Realistic Display Quantity=3522179046
@Realistic Weapons=3193673221
@Recipe Manager=3688648351
@Redbull Energy Drinks Free=3643119261
@Renegade Snork=3405220401
@Repair Case=3475678475
@Repair Improvised Suppressor=3425963998
@Repair VehicleParts=3556832931
@RepairampLoad Case and Barrel=3650376032
@RepaireRadiator=2848886993
@ResidentEvilByMHO=3528780688
@RestartWarner=3369106986
@Reverse Beartrap=3604117579
@RF GARAGE=3407301140
@RFFS LittleBird Stalker Reskin=3654447379
@RipLilBro=3665250441
@RM Boat Cargo Bags=3430532605
@RM Packable Boats V2=3430538455
@RM Packable Boats=3426172875
@Robbie the Rabbit=3336530310
@Rosette=3665881973
@RPG-7=3299609568
@Ruger13022=3691123259
@Rust CodeLock RELIFE=2870920039
@Safe Zone Shop=3245151272
@Saga Anti Cheat=3573323416
@Sahinkaya Official Experimental=3439146930
@Sahrani=3508468461
@Sakhal Houses Adapted=3551611505
@SalvageSheetMetal=3563642068
@SALVATION SERVER MOD=3421213293
@Sampan Boat 1981=3661811769
@Sarov Map=3176642073
@Scamtopia Heli Static Asset=3395668602
@Sci-Fi CodeLock RELIFE=2913750799
@Sci-fi Storage=3610994528
@ScreenTela-=3691353559
@SCWS Armoury=2872896355
@Search For Loot Improved=3200745813
@Search_Ground=3390680836
@SeasonSystem=3686445566
@Secret Trader Chernarus map mapping=3659025922
@Senzany Pack 3=3690452284
@Set complete - Bat Baby FREE=3626113553
@Set complete - Casual Soldier FREE=3628212720
@Set complete - Duck FREE=3628066105
@Set complete - FSB Alpha FREE=3628184051
@Set complete - Mercenary FREE=3628308101
@Set complete - Military FREE=3626134734
@Set complete - Operator FREE=3626188433
@Set complete - Rabbit FREE=3628853027
@Set complete - Ranked FREE=3628363534
@Set complete - Rider FREE=3626123250
@Set complete - Russian Soldier 3 FREE=3628198856
@Set complete - Russian Soldier FREE=3628187971
@Set Oni=3434057356
@SF Leaderboard  Kill Reward Shop System=3688189591
@SF Zombie Horde Event=3689174025
@SFP_ONLY_ANIMATION=3384391271
@ShipEvents=3688032690
@SHO Improved Humvee=3477786929
@Shooting Range Targets=3420700027
@Shopping area on the coast  Trade mapping=3553062828
@SibCore Server Pack=3691237132
@SilentZ=2846049941
@SimpleMatch=2848676009
@SimpleWhistle=2938257912
@Sinfall Cocaine Bear=3579518183
@Sinystok Bunker vanilla mapping=3520771553
@SK Gate Flash - Expansion Version=3687033348
@SK Gate Flash=3686369601
@Slot for SmershVest=2991926273
@SLP_Cooking_Chicken=3609255380
@Small Flagpole=3409488132
@SmershFix=2891953603
@Smoking_A_Cigarette_WTeamT=3279662973
@snegurka=3396711314
@Snipex m100=3617588425
@Snowy Trees for Early Winter Late Fall=3391938881
@Sobr Stack Items=3018886545
@SobrMods Signal Overnight Stay=3659550946
@SPN - STALKER BOOKS AND LICENSES Full Free=3223892088
@SR_Medecine=3031177788
@SSM ARMY FURNITURE=3683849626
@SSM EMP SCANNER=3427373155
@SSM FOLDING KNIFE=3568519573
@SSM FURNITURE PACK=3446079271
@SSM GAZ 2705=3562121831
@SSM HANGAR-BASE=3585153671
@SSM HOUSE=3590325504
@SSM ITEMS=3603109956
@SSM MANSION=3594435713
@SSM OZK-66=3556565325
@SSM SCARY HOUSE=3683061965
@SSM SWORD PACK=3446083830
@SSM Tactical belt=3285540802
@SSM ZARYA=3562129108
@Stalker Anomaly and Detectors Free V2=3594760626
@Stalker Artifacts=3478498520
@Stalker Icon castom=3008074406
@Stalker Menu Free=3414883494
@STALKER RSG CLOTHING SETS=2855919626
@STALKER ShronyTayniki StashCache=2804338311
@STALKER Silence of Pripyat PvE=2900328572
@Stalker Snork=2900619539
@StalkerEmission=3575657959
@Static Military PlanesVehicles and Equipment=3617359464
@Static Vehicles=3259314995
@Steel Doors RELIFE=2914014871
@Stizzys Retro Gaming Collectables=3672962311
@Stop Engine at Speed=3308331121
@Storage Containers=3114024035
@Storage Rules by DelaDuck=3674156326
@Storm_T2310_radash=3059122571
@STR Items Fix=2954362876
@STUDDS MAPPING_FULLMAP=3691876487
@STUDDS MAPPING_KAMENKA amp RIFY=3691868712
@STUDDS MAPPING_NEAF=3691863956
@SuppressorHealth=2832894404
@SuppressorHealth=2841954923
@Survivor Animations=2918418331
@Syndicate CodeLock=3377764726
@Syndicate Ship Base=3667236804
@TACTICAL GLASSES=2897588811
@Tailor Shop=3488214485
@Tailor_Bed=3497371645
@TAILOR_BUNDLE=3543169388
@Tailor_Fur_Clothing=3556558963
@Tailor_Rugs_Cushions=3493731547
@Tailor_Trader_Ship=3589507116
@TalkingNPC-Expansion-OC=3317207638
@Tarkov Inventory Sounds=2840934050
@Tarkov Medicine OLD=3399774519
@Taser X2=3328285140
@Techs 4x4 All Terrain Vehicles=3125203834
@Techs 4x4 Hilux=2839126911
@TeleportMod=3543082029
@TemperatureZones=3460255664
@Tent AutoClose=3595389033
@TerjeMedicine MMG Slot Expansion=3591246577
@TerjeModding CannabisPlus=3495376392
@Territory Plus=3439337803
@TerritoryLimits=3688406434
@TES Tales of Emerald=2850306383
@Test repack AZT=3691923253
@TGK WeaponPack=3444350187
@TGK_BlackSpecialForce=3350772461
@The Fallen=2999852967
@The Glob Mutant=3689007844
@The Kats Pyjamas - TFRP Clothing=3311933391
@The Last of Us Mutants=2942882677
@TLC GEAR MINI=3565380863
@TLC OLDSCHOOL GEAR=3582628129
@TLD MODS BMW M2 G87=3690837497
@TLH Item Pack=3179673330
@TMX_Cars=3577554740
@Tommys RFFS=3202178699
@TOP Guns=3359711406
@TopDogMusic=3692350529
@Towing Service=3004707934
@Toyota_GT86_RB=3428930701
@Tradepost Tower=3568826750
@Trader Altar Welliton=2834465675
@TraderPlus Boatlock=3555157300
@TraderPlus_Disable_DeployVehicles=3050905352
@TraderX=3560310973
@Trailbrozer 10=3152077009
@TrashCan_SZampP=3455750547
@Traveling Trader=3050988801
@Tree Shake=3545040196
@TreeHouse=3467458480
@Treeman Mutant Creature=3430370675
@True North Vehicles Pack=3319097618
@TTL Classname Extractor=3667513314
@Tulcus Backpack=2892764542
@TWOC_KRAZ6322=3410329849
@TWOC_LX470=3379941476
@TWOC_UAZ3151=3375303062
@TWOC_X5M=3379940889
@TWOCBase_CARS=3353952310
@Umbrella Clothing=2853118020
@UMT_Boss=3432156868
@Undead Mutant Creature=3429356963
@Underground Trigger Creator for DayZ Editor=3547799357
@UniversalSuppressor=2845154537
@Unlimited Stamina Outdated=3131502760
@UPDATED LMs Semi Truck amp Trailer - Vehicle=3485290555
@UStamina=3457638650
@V2 Marquee Letter Signs=3660152067
@Van=3468538122
@VanillaAttachmentExpansion=3331680442
@VanillaCarsUp=2883750099
@VanillaCarUpgrade=3456484853
@Vazap_Drone=3277850664
@Vazap_Weapons=2854948642
@Vehicle Cover=3442752056
@Vehicle Shooting=3436144357
@Vehicle Status RELIFE=3442217930
@Vehicle v20=2893376090
@VehicleRecovery=3425994533
@Vending Machine Free=3664693178
@Venom Mask=2860912286
@ViewInventoryAnimation=2833363308
@VIRTUAL_STORAGE=3686229505
@Visual Effect Point=3407573486
@vlads mega map making assets=3624064154
@Volkswagen Passat TSI FREE=3560400234
@Vost_Cassettes=2991267574
@VVN_Greenhouse=3664943616
@VVN_Old_refrigerator=3592127439
@VVN_Wind_generator=3603995798
@Waraeger Armory Mega Asset Pack=3556348051
@Waraeger Armory Zryachiy Clothing Set=3633802307
@Watchcorp Hairs=3513750352
@Watchcorp Tattoos=3498571761
@Water Tank RELIFE=2912833777
@Waterplant Building=3501986165
@We Will Survive=3691072802
@Weapons Repeair  and Sewing Kit FREE=3323375076
@Weather Lighting Control WLC=3660359997
@Welcome amp Interval Messages=3564276812
@WendigoCreature=2882158476
@Werewolf=3331449728
@Winter Chernarus=1597140327
@WL_Chess=3246054814
@WL_Collection_of_models=3410781054
@Wolf Catch Eggs RELIFE=3470181762
@Wooden Chalk Sign RELIFE=3452928091
@Workbench_EXP=3601149469
@Workbench=2895049000
@Working Static Radio Example Sound Script=3580142035
@Xom_Barrel=3617576656
@Xom_Case=3616465228
@Yeti=3661868549
@YukiImbicile=2834315291
@Zen GPS Watch For Expansion=3684475101
@Zens Car Compass=3526892683
@Zens Car Workbench=3030756548
@Zens COT=3610102625
@Zens Crafting Sounds=3043440124
@Zens Custom Hints=3579870653
@Zens Enormous Package=3288979834
@Zens ExpansionAI Audio=3321340470
@Zens Fire Sticks=3498006019
@Zens Firewood Piles=2877877368
@Zens GPS Watch=3490580002
@Zens Immersive Chat HUD=3369993498
@Zens Immersive Login=2924719512
@Zens Lazy Actions=3568541011
@Zens Map Enhancement=3483440991
@Zens Music=3412251200
@Zens Notes=2857994912
@Zens Proxy Actions=3395567006
@Zens Rebuildable Wells=3540107459
@Zens Shove Mod=3493529779
@Zens Skill Perk Tree=3601119520
@Zens Sleeping Mod=3468961047
@Zens Tire Rack=3015212770
@Zens Treasure=3426979799
@Zens Zippo Lighter=3170285574
@Zens Zombie Door Bangers=2932842394
@ZenSleep_ST=3434633875
@ZenSleep=2799531123
@ZeroDawnBRCore=3690440996
@ZFServerLogs=3161386029
@zm workbench=3643931184
@ZMG Boat Kit Mod=3386757936
@Zombi_Pack_ByHare=3552086598
@Zombie Eidolon=3662317842
@Zombie psychopath=3576644061
@zombie_Nun=3544328349
@ZombieHealthBar=3637761820
@Zombies_With_Guns=3669662598
@Zoom_While_Sprint=2935307787
@zSpawnSelection=2017605880
@МОНОХРОМ=3687152456
"""


# Mod dependency map: {workshop_id: {name, deps: {dep_workshop_id: dep_name}}}
# Used by mod_ops to prompt for installing/removing required dependencies.
MOD_DEPS: dict[str, dict] = {
    "2458896948": {
        "name": "@TraderPlus",
        "deps": {"1559212036": "@CF"},
    },
    # config.cpp: requiredAddons[] = {"DayZExpansion_AI_Scripts"} (plans/47).
    # Expansion-AI's own closure (Core, CF, Dabs) comes with it.
    "2874589934": {
        "name": "@DayZ-Dynamic-AI-Addon",
        "deps": {"2792982069": "@DayZ-Expansion-AI"},
    },
    # Workshop page: "Required items" lists @CF only (plans/48).
    "2294323732": {
        "name": "@RadZone",
        "deps": {"1559212036": "@CF"},
    },
    "1752669393": {
        "name": "@ToxicZone",
        "deps": {"1559212036": "@CF"},
    },
    # Workshop pages' "Required items" (2026 Q4). Neither config.cpp names
    # the dependency in requiredAddons, so the PBO scan cannot see it; without
    # @Survivor Animations the World module fails on CustomVehicleAnimInstances
    # and the server segfaults at boot.
    "2971190303": {
        "name": "@DayZ-Bicycle",
        "deps": {"2918418331": "@Survivor Animations"},
    },
    "2971993210": {
        "name": "@Arma Bicycles",
        "deps": {"2971190303": "@DayZ-Bicycle"},
    },
}

# A credit line the mod page shows for a mod, keyed by Workshop ID:
# {workshop_id: {text, link_label, link_url}}. TheDmitri makes TraderPlus,
# RadZone and ToxicZone, and Zedstead names his free Beans Launcher on their pages.
_THEDMITRI_CREDIT = {
    "text": "Made by TheDmitri. He also makes Beans Launcher, a free launcher for players:",
    "link_label": "dayzbeanslauncher.com",
    "link_url": "https://dayzbeanslauncher.com/",
}
MOD_CREDITS: dict[str, dict[str, str]] = {
    "2458896948": _THEDMITRI_CREDIT,  # @TraderPlus
    "2294323732": _THEDMITRI_CREDIT,  # @RadZone
    "1752669393": _THEDMITRI_CREDIT,  # @ToxicZone
}

# Workshop IDs of mods that belong in -serverMod=, not -mod=: server-only code
# the players never download. Installing one marks it server-only
# in mods.json; the per-mod toggle on the Mods page can undo
# that. Only list a mod whose author says so on its Workshop page. Moving a mod
# with content players see (items, models, UI) to -serverMod= breaks it for
# them.
SERVER_SIDE_MOD_IDS: frozenset[str] = frozenset({
    "2874589934",  # DayZ-Dynamic-AI-Addon: "can be listed in servermod list only"
})

VANILLA_MAPS = {
    "Chernarus": {"mission": "dayzOffline.chernarusplus"},
    "Livonia": {"mission": "dayzOffline.enoch"},
    # `dlc` names the paid DLC a player needs to join, and `dlc_note` says it
    # on Quick setup and the Maps page. Livonia is in the base game since
    # update 1.25 (May 2024), so it has none.
    "Sakhal": {
        "mission": "dayzOffline.sakhal",
        "dlc": "Frostline",
        "dlc_note": "Sakhal comes with the paid Frostline DLC. A player who does not own it cannot join.",
    },
    # Badlands (DayZ 1.30, 2026 Q4). The mission folder and the world size
    # were read from a 1.30 stable download (build 25460313).
    # Whether a player needs the DLC to join was not confirmed by Bohemia when
    # this was written (2026-11-09), so the note asks for a join test.
    "Badlands": {
        "mission": "dayzOffline.nasdara",
        "dlc": "Badlands",
        "dlc_note": (
            "Nasdara comes with the paid Badlands DLC. Make sure that your players own it, "
            "or test a join from an account without it first."
        ),
    },
    # An official map that is announced but not shipped goes here as
    # {"mission": None, "terrain": <world>, "released": False, "release": <text>,
    # "url": <store page>}. `mission` stays None until Bohemia ships the folder:
    # a guessed name would let a switch copy a mission that does not exist.
    # `map_ops.vanilla_mission` finds a `<anything>.<terrain>` folder once a
    # server update has put it in mpmissions/, and until then the switch paths
    # refuse the map (`map_ops.map_available`). When it ships: fill in the real
    # mission, drop `terrain`/`released`/`release`/`url`, and add its terrain to
    # MAP_SIZES (and app/spawn_editor/converters.py's MAP_CALIBRATION).
}


def map_is_released(name: str) -> bool:
    """False for a map zsm lists but cannot install yet.

    An unreleased entry is display-only: there is no mission to copy and no mod
    to download, so both switch paths (``zsm maps switch`` and
    ``POST /api/maps/switch``) refuse it rather than act on its empty mission.
    Unknown names answer True — "unknown map" is the caller's own error to
    raise, and it already does.
    """
    data = VANILLA_MAPS.get(name) or WORKSHOP_MAPS.get(name) or {}
    return data.get("released", True)

# Workshop maps: each entry defines the mod, deps, and mission source.
# Mission source is either "zip" (download from URL) or "mod" (extract from workshop mod).
# See Bitterroot setup: https://github.com/Mattty-Ice/Bitterroot-DayZ
WORKSHOP_MAPS = {
    "Namalsk": {
        "mods": {"@Namalsk Island": "2289456201"},
        "deps": {"@Namalsk Survival": "2289461232"},
        "mission": {
            "template": "regular.namalsk",
            "source": "zip",
            "zip_url": "https://github.com/SumrakDZN/Namalsk-Server/archive/refs/heads/main.zip",
            "mission_path": "Mission Files/regular.namalsk",
            "server_cfg_path": "Server Config/Regular/serverDZ.cfg",
        },
    },
    "Namalsk Hardcore": {
        "mods": {"@Namalsk Island": "2289456201"},
        "deps": {"@Namalsk Survival": "2289461232"},
        "mission": {
            "template": "hardcore.namalsk",
            "source": "zip",
            "zip_url": "https://github.com/SumrakDZN/Namalsk-Server/archive/refs/heads/main.zip",
            "mission_path": "Mission Files/hardcore.namalsk",
            "server_cfg_path": "Server Config/Hardcore/serverDZ.cfg",
        },
    },
    "Deer Isle": {
        "mods": {"@DeerIsle": "1602372402"},
        "deps": {"@CF": "1559212036"},
        "mission": {
            "template": "empty.deerisle",
            "source": "zip",
            "zip_url": "https://github.com/johnmclane666/Deerisle-Stable/archive/refs/heads/master.zip",
            "mission_path": "V5.9/empty.deerisle",
            "server_cfg_path": None,
        },
    },
    "Banov": {
        "mods": {"@Banov": "2415195639"},
        "deps": {"@Central European Asset Pack": "2330497955"},
        "mission": {
            # The repo's folder is dayzOffline.banov since upstream deleted
            # empty.banov (f040b801, 2026 Q2). The template keeps the old
            # name: installed servers are recognised by it, and DayZ needs
            # only the .banov suffix.
            "template": "empty.banov",
            "source": "zip",
            "zip_url": "https://github.com/KubeloLive/Banov-Central-Economy/archive/refs/heads/main.zip",
            "mission_path": "dayzOffline.banov",
            "server_cfg_path": None,
        },
    },
    "Banov Frost": {
        "mods": {"@BanovFrost": "3375557599"},
        "deps": {"@Central European Asset Pack": "2330497955"},
        "mission": {
            "template": "empty.banovfrost",
            "source": "zip",
            "zip_url": "https://github.com/KubeloLive/Banov-Central-Economy/archive/refs/heads/main.zip",
            "mission_path": "empty.banovfrost",
            "server_cfg_path": None,
        },
    },
    "Esseker": {
        "mods": {"@Esseker": "2462896799"},
        "deps": {},
        "mission": {
            "template": "dayzOffline.Esseker",
            "source": "zip",
            "zip_url": "https://github.com/InclementDab/Esseker-Server/archive/refs/heads/main.zip",
            "mission_path": "Mission Files/dayzOffline.Esseker",
            "server_cfg_path": None,
        },
    },
    "Bitterroot": {
        "mods": {"@Bitterroot": "2906823750"},
        "deps": {},
        "mission": {
            "template": "empty.Bitterroot",
            "source": "zip",
            "zip_url": "https://github.com/Mattty-Ice/Bitterroot-DayZ/archive/refs/heads/main.zip",
            "mission_path": "empty.Bitterroot",
            "server_cfg_path": None,
        },
    },
    "TakistanPlus": {
        "mods": {"@TakistanPlus": "2563233742"},
        "deps": {"@Dabs Framework": "2545327648"},
        "mission": {
            "template": "dayzOffline.takistanplus",
            "source": "zip",
            "zip_url": "https://github.com/CypeR79/DayZ-Projects/archive/refs/heads/main.zip",
            "mission_path": "TakistanPlus/mission",
            "server_cfg_path": None,
        },
    }
}


# One-line "what is this map" blurbs shown on the web Maps page, keyed by the
# display names used in VANILLA_MAPS / WORKSHOP_MAPS. The facts come from the
# official Bohemia pages and each map's Steam Workshop or repo description; the
# wording is our own, never a copy of theirs (plans/86).
MAP_DESCRIPTIONS: dict[str, str] = {
    # Vanilla
    "Chernarus": (
        "DayZ's default terrain: 230 km2 of a post-Soviet republic modelled on the "
        "Czech countryside — a southern coastline of fishing towns, farmland and "
        "forest inland, and the heaviest military loot in the north-west."
    ),
    "Livonia": (
        "The Enoch terrain, part of the base game since update 1.25: 168 km2 of dense Central European "
        "forest, farmland, lakes and swamp, with a late-summer climate of frequent "
        "heavy rain and thunderstorms."
    ),
    "Sakhal": (
        "The Frostline expansion's map and the smallest official one: about 83 km2 "
        "of volcanic islands, not counting the ice sheets, with snowy forest, hilltop "
        "villages and an abandoned naval base, at sub-zero temperatures."
    ),
    "Badlands": (
        "The Badlands expansion's Nasdara province, the largest official map at "
        "267 km2: a desert west of Chernarus with towns, oil fields and old "
        "military bases. Water is scarce, sandstorms blow through, and many ruined "
        "buildings can be rebuilt."
    ),
    # Workshop
    "Namalsk": (
        "Sumrak's arctic archipelago — small, dense and deliberately punishing, with "
        "EVR blowout storms, the Athena-2 underground lab and constant hypothermia "
        "pressure. This entry uses Sumrak's regular setup, with vehicles, base "
        "building and November weather."
    ),
    "Namalsk Hardcore": (
        "Namalsk on Sumrak's harder setup. Survivors go without vehicles and keep "
        "their gear only in stashes and small tents, guns and ammo are rare, loot "
        "stays longer, and the weather is December cold."
    ),
    "Deer Isle": (
        "A 256 km2 island inspired by the coast of Maine: fishing towns, dense forest, "
        "abandoned military sites and underground bunkers, plus custom points of "
        "interest and a questline."
    ),
    "Banov": (
        "A map of the Banov district in Slovakia: a city, dozens of villages, three "
        "airfields and two reservoirs among forest, swamp and rivers."
    ),
    "Banov Frost": (
        "The winter version of Banov — the same Slovak terrain under deep snow and "
        "frozen rivers."
    ),
    "Esseker": (
        "RonHill's Arma 3 terrain ported to DayZ: 169 km2 of a Bosnia-inspired region "
        "reclaimed by nature, with Esseker city, the Plava Vrana military complex "
        "and a zoo. Last updated in 2021 and no longer developed."
    ),
    "Bitterroot": (
        "A made-up corner of rural Montana with a cult in its backstory. The map adds "
        "American weapons and wildlife such as moose, black bears and mountain lions."
    ),
    "TakistanPlus": (
        "Takistan from Arma 2, rebuilt for DayZ: a dry province of high mountains "
        "and desert, with small villages far apart and long views over open ground."
    ),
}


# Map terrain sizes in meters (used for coordinate → pixel conversion in map viewer)
MAP_SIZES: dict[str, int] = {
    "chernarusplus": 15360,
    "enoch": 12800,
    "sakhal": 15360,
    # The header of the stock areaflags.map holds the world size (1.30).
    "nasdara": 16384,
    "namalsk": 12800,
    "deerisle": 16384,
    "banov": 15360,
    "banovfrost": 15360,
    "esseker": 12800,
    "bitterroot": 12288,
    "takistanplus": 12800,
}


def parse_workshop_mod_ids() -> dict[str, str]:
    """Parse WORKSHOP_MOD_IDS into {workshop_id: '@ModName'}."""
    from app.modmgr.mods import split_name_id

    result: dict[str, str] = {}
    for line in WORKSHOP_MOD_IDS.strip().splitlines():
        entry = split_name_id(line)
        if entry:
            name, wid = entry
            result[wid] = name
    return result
