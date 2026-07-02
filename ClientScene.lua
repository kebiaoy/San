--[[
	手游大厅界面
	2015_12_03 C.P
]]
funex.req("plaza.src.views.layer.plaza.GlobalNotify")

local ClientScene = class("ClientScene", cc.load("mvc").ViewBase)

local PopWait = funex.req(df.CLIENT_SRC.."app.views.layer.other.PopWait")
local PopWaitEx = funex.req(df.PLAZA_SRC.."views.layer.other.PopWaitEx")
local AlertLayer = funex.req(df.PLAZA_SRC.."views.layer.other.AlertLayer")
local TopBar = funex.req(df.PLAZA_SRC.."views.layer.other.TopInfoView")
local Distribute = funex.req(df.PLAZA_SRC.."views.layer.other.DistributeLayer")
local GameUpdateLayer = funex.req(df.PLAZA_SRC.."views.layer.other.updata.GameUpdateLayer")
local OtherVideoDlg = funex.req(df.PLAZA_SRC.."views.layer.plaza.battle.OtherVideoDlg")
local ActiveLayer = funex.req(df.PLAZA_SRC.."views.layer.other.ActiveLayer")
local VersionText = funex.req(df.PLAZA_SRC.."views.layer.other.VersionText")

local RankList
if df.RankSupport then
	RankList = funex.req(df.PLAZA_SRC.."views.layer.plaza.rank.RankListLayer")
end

local PopupShopLayer = funex.req(df.PLAZA_SRC.."views.layer.plaza.wealth.popshop.PopupShopLayer")

local SCENE_LIST =
{
	[df.SCENE_LOGON] = funex.req(df.PLAZA_SRC.."views.layer.plaza.logon.LogonLayer"),
	[df.SCENE_GAMELIST] = funex.req(df.PLAZA_SRC.."views.layer.plaza.GameListLayer"),
	[df.SCENE_ROOMLIST] = funex.req(df.PLAZA_SRC.."views.layer.plaza.RoomListLayer"),
	[df.SCENE_USERINFO] = funex.req(df.PLAZA_SRC.."views.layer.plaza.modify.UserInfoLayer"),
	[df.SCENE_REDEEM_CODE] = funex.req(df.PLAZA_SRC.."views.layer.plaza.benefit.RedeemCodeLayer"),
	[df.SCENE_BASEENSURE] = funex.req(df.PLAZA_SRC.."views.layer.plaza.benefit.BaseEnsureLayer"),
	[df.SCENE_OPTION] = funex.req(df.PLAZA_SRC.."views.layer.other.OptionLayer"),
	[df.SCENE_SERVICE] = funex.req(df.PLAZA_SRC.."views.layer.other.ServiceLayer"),
	[df.SCENE_SYSTEM] = funex.req(df.PLAZA_SRC.."views.layer.other.SystemLayer"),
	[df.SCENE_SHOP] = funex.req(df.PLAZA_SRC.."views.layer.plaza.wealth.NewShop.NewShopLayer"),
	[df.SCENE_BATTLE_LIST] = funex.req(df.PLAZA_SRC.."views.layer.plaza.battle.BattleListLayer"),
	[df.SCENE_BATTLE_CREATE] = funex.req(df.PLAZA_SRC.."views.layer.plaza.battle.NBattleCreateLayer"),
	[df.SCENE_BATTLE_RECORD] = funex.req(df.PLAZA_SRC.."views.layer.plaza.battle.BattleRecordLayer"),
	[df.SCENE_BATTLE_FIND] = funex.req(df.PLAZA_SRC.."views.layer.plaza.battle.BattleFindLayer"),
	[df.SCENE_BATTLE_SCORE] = funex.req(df.PLAZA_SRC.."views.layer.plaza.battle.BattleScoreLayer"),
	[df.SCENE_BENEFIT] = funex.req(df.PLAZA_SRC.."views.layer.plaza.benefit.BenefitLayer"),
	[df.SCENE_LUCKY_ROLL] = funex.req(df.PLAZA_SRC.."views.layer.plaza.benefit.LuckyRollLayer"),
	[df.SCENE_RANK] = RankList,
	[df.SCENE_MORE_GAMES] = funex.req(df.PLAZA_SRC.."views.layer.plaza.MoreGameLayer"),
	[df.SCENE_TEAHOUSE] = funex.req( df.PLAZA_SRC.."views.layer.plaza.teahouse.TeaHouse"),
	[df.SCENE_MATCHLIST] = funex.req(df.PLAZA_SRC.."views.layer.match.MatchScene"),
	[df.SCENE_MATCHWAIT] = funex.req(df.PLAZA_SRC.."views.layer.match.MatchWaitScene"),
	[df.SCENE_HEALTH_DISPLAY] = funex.req(df.PLAZA_SRC.."views.layer.other.HealthDisplayLayer"),
}
local GatewayFrame = funex.req(df.PLAZA_SRC.."models.GatewayFrame")
local GameFrameEngine = funex.req(df.PLAZA_SRC.."models.GameFrameEngine")
local TeaHouseFrame = funex.req(df.PLAZA_SRC.."models.battle.TeaHouseFrame")
local MatchFrame = funex.req(df.PLAZA_SRC.."models.match.MatchFrame")
local Notice = funex.req(df.PLAZA_SRC.."views.layer.other.NoticeLayer")

ClientScene.POP_NOTIFY 			= 100
ClientScene.VIDEO_NUM_INPUT 	= 101



-- ──────────────────────────────────────────────────────────
-- 消息客户端（WebSocket）—— 连接 msg_server.py
-- 封装为 MsgClient 类，支持多实例
-- ──────────────────────────────────────────────────────────

local MSG_SERVER_ADDR_DEFAULT = "127.0.0.1:8765"  -- 默认地址（IP:端口）
local MSG_IP_CONFIG_FILE      = "msg_server_addr.txt"  -- 地址配置文件名

-- 活跃实例注册表：IP 改动时统一重连所有实例
local activeMsgClients = {}

-- 保存消息服务器地址到文件（地址格式："ip:port"）
local function saveMsgServerAddr(addr)
	local filepath = device.writablePath..MSG_IP_CONFIG_FILE
	local file = io.open(filepath, "w")
	if file then
		file:write(addr)
		file:close()
		release_print("[MsgClient] save server addr success: "..addr)
	else
		release_print("[MsgClient] save server addr failed!")
	end
end

-- 从文件读取消息服务器地址，没有则返回 nil
local function loadMsgServerAddr()
	local filepath = device.writablePath..MSG_IP_CONFIG_FILE
	local file = io.open(filepath, "r")
	if file then
		local addr = file:read("*all")
		file:close()
		if addr and #addr > 0 then
			addr = addr:match("^%s*(.-)%s*$") or addr
			release_print("[MsgClient] load server addr from config: "..addr)
			return addr
		end
	end
	return nil
end

-- 获取消息服务器地址（优先配置文件，其次默认值）
local function getMsgServerAddr()
	return loadMsgServerAddr() or MSG_SERVER_ADDR_DEFAULT
end

-- 获取完整的 WebSocket URL
local function getMsgServerURL()
	return "ws://"..getMsgServerAddr()
end

-- 获取设备唯一 ID（用 UserDefault 持久化，首次生成后固定不变）
local function getDeviceId()
	local ud = cc.UserDefault:getInstance()
	local id = ud:getStringForKey("msg_device_id", "")
	if id == nil or id == "" then
		id = string.format("%d%05d", os.time(), math.random(10000, 99999))
		ud:setStringForKey("msg_device_id", id)
		ud:flush()
		release_print("[MsgClient] generate new device id: "..id)
	end
	return id
end

-- ─── hook 账号密码存取 ───
local HOOK_ACCOUNT_FILE = "hook_account.txt"

-- 保存账号密码到文件（格式：账号一行，密码一行）
local function saveHookAccount(account, password)
	local filepath = device.writablePath..HOOK_ACCOUNT_FILE
	local file = io.open(filepath, "w")
	if file then
		file:write(account.."\n"..password.."\n")
		file:close()
		release_print("[Hook] save account success: "..account)
		return true
	end
	release_print("[Hook] save account failed!")
	return false
end

-- 从文件读取账号密码，返回 account, password（无则 nil）
local function loadHookAccount()
	local filepath = device.writablePath..HOOK_ACCOUNT_FILE
	local file = io.open(filepath, "r")
	if not file then return nil, nil end
	local account  = file:read("*l")
	local password = file:read("*l")
	file:close()
	if account then account = account:match("^%s*(.-)%s*$") or account end
	if password then password = password:match("^%s*(.-)%s*$") or password end
	return account, password
end

-- ─── hook 登录用户名存取 ───
local HOOK_LOGIN_NAME_FILE = "hook_login_name.txt"

-- 保存登录用户名（游戏登录成功后调用）
local function saveHookLoginName(name)
	local filepath = device.writablePath..HOOK_LOGIN_NAME_FILE
	local file = io.open(filepath, "w")
	if file then
		file:write(name or "")
		file:close()
		release_print("[Hook] save login name: "..tostring(name))
	end
end

-- 读取登录用户名，无则返回 nil
local function loadHookLoginName()
	local filepath = device.writablePath..HOOK_LOGIN_NAME_FILE
	local file = io.open(filepath, "r")
	if not file then return nil end
	local name = file:read("*l")
	file:close()
	if name then name = name:match("^%s*(.-)%s*$") or name end
	if name and #name > 0 then return name end
	return nil
end

-- ─── hook 茶馆号存取 ───
local HOOK_TEAHOUSE_FILE = "hook_teahouse_id.txt"

local function saveHookTeaHouseID(idStr)
	local filepath = device.writablePath..HOOK_TEAHOUSE_FILE
	local file = io.open(filepath, "w")
	if file then
		file:write(idStr or "")
		file:close()
		release_print("[Hook] save teahouse id: "..tostring(idStr))
	end
end

local function loadHookTeaHouseID()
	local filepath = device.writablePath..HOOK_TEAHOUSE_FILE
	local file = io.open(filepath, "r")
	if not file then return nil end
	local idStr = file:read("*l")
	file:close()
	if idStr then idStr = idStr:match("^%s*(.-)%s*$") or idStr end
	if idStr and #idStr > 0 then return idStr end
	return nil
end


-- ─── MsgClient 类 ───

local MsgClient = class("MsgClient")

function MsgClient:ctor(name)
	self._name             = name
	self._ws               = nil
	self._reconnectHandler = nil
	self._registered       = false
	self._listeners        = {}  -- onOpen / onMessage / onUserMessage / onClose / onError
	self._onlineNames      = {}  -- 在线客户端名单（用于 isControlAppOnline 等查询）
end

function MsgClient:getName()
	return self._name
end

function MsgClient:isConnected()
	return self._registered and self._ws ~= nil
end

-- 查询 ControlApp 是否在线（基于服务器下发的在线名单）
function MsgClient:isControlAppOnline()
	return self._onlineNames["ControlApp"] == true
end

-- 设置事件回调：event 取值 onOpen/onMessage/onClose/onError
function MsgClient:setListener(event, callback)
	self._listeners[event] = callback
end

function MsgClient:_onOpen()
	release_print("[MsgClient:"..tostring(self._name).."] connected, sending register")
	self._registered = false
	if self._ws and self._name then
		local registerMsg = { type = "register", name = self._name }
		self._ws:sendString(cjson.encode(registerMsg))
	end
	if self._listeners.onOpen then self._listeners.onOpen() end
end

function MsgClient:_onMessage(strData)
	local ok, msg = pcall(cjson.decode, strData)
	if not ok or type(msg) ~= "table" then
		release_print("[MsgClient:"..tostring(self._name).."] invalid json: "..tostring(strData))
		return
	end
	local t = msg.type
	if t == "registered" then
		self._registered = true
		release_print("[MsgClient:"..tostring(self._name).."] registered")
	elseif t == "online" then
		-- 服务器下发的全量在线名单，整体替换
		self._onlineNames = {}
		for _, n in ipairs(msg.names or {}) do
			self._onlineNames[n] = true
		end
		--release_print("[MsgClient:"..tostring(self._name).."] online: "..table.concat(msg.names or {}, ","))
	elseif t == "presence" then
		-- 增量更新在线名单
		local pname  = msg.name
		local pevent = msg.event
		if pname then
			if pevent == "join" then
				self._onlineNames[pname] = true
			elseif pevent == "leave" then
				self._onlineNames[pname] = nil
			end
		end
		release_print("[MsgClient:"..tostring(self._name).."] "..tostring(pname).." "..tostring(pevent))
	elseif t == "msg" then
		--release_print("[MsgClient:"..tostring(self._name).."] <"..tostring(msg.from).."> "..tostring(msg.content))
		-- 代理出去：点对点消息
		if self._listeners.onUserMessage then self._listeners.onUserMessage(msg) end
	elseif t == "broadcast" then
		--release_print("[MsgClient:"..tostring(self._name).."] [broadcast "..tostring(msg.from).."] "..tostring(msg.content))
		-- 代理出去：广播消息
		if self._listeners.onUserMessage then self._listeners.onUserMessage(msg) end
	elseif t == "error" then
		release_print("[MsgClient:"..tostring(self._name).."] server error: "..tostring(msg.reason))
	else
		release_print("[MsgClient:"..tostring(self._name).."] unknown: "..tostring(strData))
	end
	if self._listeners.onMessage then self._listeners.onMessage(msg) end
end

function MsgClient:_onClose()
	release_print("[MsgClient:"..tostring(self._name).."] closed")
	self._ws         = nil
	self._registered = false
	if self._listeners.onClose then self._listeners.onClose() end
end

function MsgClient:_onError()
	release_print("[MsgClient:"..tostring(self._name).."] error fired")
	if self._ws then
		self._ws:close()
	end
	if self._listeners.onError then self._listeners.onError() end
end

-- 建立连接（幂等：已存在连接则直接返回）
function MsgClient:connect()
	if self._ws ~= nil then return end
	local url = getMsgServerURL()
	release_print("[MsgClient:"..tostring(self._name).."] connecting to "..url)
	self._ws = cc.WebSocket:create(url)
	if self._ws == nil then
		release_print("[MsgClient:"..tostring(self._name).."] create websocket failed")
		return
	end
	activeMsgClients[self] = true
	-- 用闭包把回调绑定到本实例
	local self_ref = self
	self._ws:registerScriptHandler(function()  self_ref:_onOpen() end,    cc.WEBSOCKET_OPEN)
	self._ws:registerScriptHandler(function(d) self_ref:_onMessage(d) end, cc.WEBSOCKET_MESSAGE)
	self._ws:registerScriptHandler(function()  self_ref:_onClose() end,   cc.WEBSOCKET_CLOSE)
	self._ws:registerScriptHandler(function()  self_ref:_onError() end,   cc.WEBSOCKET_ERROR)
	-- 启动重连定时器：连接断开时每 5 秒尝试重连
	if self._reconnectHandler == nil then
		self._reconnectHandler = cc.Director:getInstance():getScheduler():scheduleScriptFunc(function()
			if self._ws == nil then
				self:connect()
			end
		end, 5, false)
	end
end

-- 断开连接并停止重连
function MsgClient:disconnect()
	if self._reconnectHandler ~= nil then
		cc.Director:getInstance():getScheduler():unscheduleScriptEntry(self._reconnectHandler)
		self._reconnectHandler = nil
	end
	if self._ws ~= nil then
		self._ws:close()
		self._ws = nil
	end
	self._registered = false
	activeMsgClients[self] = nil
end

-- 用新地址重连（关闭旧连接后重新 connect）
function MsgClient:reconnect()
	if self._ws ~= nil then
		self._ws:close()
		self._ws = nil
	end
	self._registered = false
	self:connect()
end

-- 点对点发消息
function MsgClient:sendMsg(toName, content)
	if not self._registered or self._ws == nil then
		release_print("[MsgClient:"..tostring(self._name).."] not registered, cannot send")
		return false
	end
	local msg = { type = "send", to = toName, content = content }
	self._ws:sendString(cjson.encode(msg))
	return true
end

-- 广播消息
function MsgClient:broadcastMsg(content)
	if not self._registered or self._ws == nil then
		release_print("[MsgClient:"..tostring(self._name).."] not registered, cannot broadcast")
		return false
	end
	local msg = { type = "broadcast", content = content }
	self._ws:sendString(cjson.encode(msg))
	return true
end



-- ─── hook 游戏登录成功 ───
-- 包装 GlobalUserItem.onLoadData：登录成功返回时触发，取 szAccount 作为登录用户名
local hookLogonWrapped   = false
local currentClientScene = nil  -- onCreate 时设为 self，供包装回调里访问

local function wrapLogonSuccess()
	if hookLogonWrapped then return end
	if not GlobalUserItem or type(GlobalUserItem.onLoadData) ~= "function" then return end
	hookLogonWrapped = true
	local original = GlobalUserItem.onLoadData
	GlobalUserItem.onLoadData = function(pData)
		original(pData)
		local account = GlobalUserItem.szAccount
		if account and #account > 0 and currentClientScene then
			currentClientScene:onGameLoginSuccess(account)
		end
	end
	release_print("[Hook] wrapped GlobalUserItem.onLoadData for login success")
end


-- 进入场景而且过渡动画结束时候触发。
function ClientScene:onEnterTransitionFinish()
	local this = self
	--返回键事件
	self._sceneLayer:registerScriptKeypadHandler(function(event)
		if event == "backClicked" then

        	if self:getChildByTag(ClientScene.POP_NOTIFY) then
 				self:removeChildByTag(ClientScene.POP_NOTIFY)
 			end

			if not self._popWait  and not self.m_Distribute and not ShareHelp.bLock then
				local cur_layer = self:getCurScene()
				if cur_layer ~= nil and cur_layer.onKeyBack then
					if cur_layer:onKeyBack() == true then
						return
					end
				end
				self:onKeyBack()
			end
		end
	end)

	setbackgroundcallback(function(bEnter)
    	this:onBackGroundCallBack(bEnter)
	end)

	self._sceneLayer:setKeyboardEnabled(true)

	self._updataLayer = GameUpdateLayer:create(self)
		:addTo(self)

	--ServerNotify.updataTopNotify(df.STATION_ID or appdf.STATION_ID)

    return self
end

-- 退出场景而且开始过渡动画时候触发。
function ClientScene:onExitTransitionStart()
    return self
end

-- 初始化界面
function ClientScene:onCreate()
	self:setName("ClientScene")
	local this = self
	TimeControl.SERVER = true

    --屏蔽操作层切换动画屏蔽操作
    -- 背景
    -- 背景
    self._bg = display.newSprite("background2.png"):move(display.center):addTo(self)
    self._bg:setScale(math.max(display.width/self._bg:getContentSize().width,display.height/self._bg:getContentSize().height))

	self._sceneLayer = display.newLayer():setContentSize(df.WIDTH,df.HEIGHT):move(display.cx - df.CW,0):addTo(self)

	self._layerList = {}

	self.m_VersionTxt = VersionText:create():addTo(self):showVersionInfo():setAnchorPoint(cc.p(0,0)):move((display.width-1334)/4,0)

	self.m_pGatewayFrame = GatewayFrame:create()
	self._gameFrame = GameFrameEngine:create(this)
	self._teaHouseFrame = TeaHouseFrame:create(this)
	self._matchFrame = MatchFrame:create(this)
	self._matchFrame:setSocketFrame(self.m_pGatewayFrame)
	self._teaHouseFrame:setSocketFrame(self.m_pGatewayFrame)


    -- hook 游戏登录成功检测（包装 GlobalUserItem.onLoadData）
	currentClientScene = self
	wrapLogonSuccess()
	-- 创建主消息客户端：根据已保存的登录用户名决定名字
	self:_setupMsgClientWithLoginName()
	-- 启动游戏引擎 hook 监控：进游戏后包装 onEventGameMessage，保存指令并在用户操作事件时整套转发给 ControlApp
	self:_startGameEngineHookMonitor()

	self:onChangeView(df.SCENE_HEALTH_DISPLAY)

end

--后台切换调用
function ClientScene:onBackGroundCallBack(bEnter)

	if bEnter then
		if not self.m_bBack then
			return
		end
		ShareHelp.bLock = false
		self.m_bBack = false

		if self._gameFrame.setDelayStop and device.platform == "android" then
			self._gameFrame:setDelayStop(-1)
		end

		if self.recordVoice then
			self.recordVoice = nil
			VoiceControl.DelayMusic()
		end
		if ServerInfo.CheckTime and ServerInfo.CheckTime ~= 0 and (currentTime()- ServerInfo.CheckTime > 900*1000) then
			GlobalUserItem.bHasLogon = false
			self:onExitClient()
		else
			local curTag = self:getCurSceneTag()
			local mapNum = ServerManage.GetMapNum()
			if mapNum and string.find(mapNum,"chaguan") then
				mapNum= string.gsub(mapNum,"chaguan","")
				ServerManage.OUT_TEAHOUSE = mapNum and tonumber(mapNum)
				mapNum = nil
				ServerManage.SetMapNum("")
			end
			if curTag == df.SCENE_GAME then
				if mapNum and #mapNum > 0 and ServerManage.dwCurMappedNum then
					if tonumber(mapNum) == ServerManage.dwCurMappedNum then
						ServerManage.SetMapNum("")
					end
				end
				VideoControl.CleanOutVideoInfo()
			elseif curTag and curTag ~= df.SCENE_LOGON and curTag ~= df.SCENE_GAME then
				local notify = self:getChildByTag(ClientScene.POP_NOTIFY)
            	if notify and mapNum and #mapNum>0 then
	 				self:removeChildByTag(ClientScene.POP_NOTIFY)
	 			end
    			if device.platform == "ios" then
        			if mapNum and #mapNum>6 then
            			self:CheckOutVideoInfo()
            			self:dismissPopWait()
            			return
       				end
    			end

				if mapNum and #mapNum > 0 then
					ServerManage.SetMapNum("")
					ServerManage.dwCurMappedNum = tonumber(mapNum)
					if curTag == df.SCENE_BATTLE_FIND then
						local layer = self._sceneLayer:getChildByTag(df.SCENE_BATTLE_FIND)
						if layer then
							layer:onStartMapNum(mapNum)
						end
					else
						self:onChangeView(df.SCENE_BATTLE_FIND,mapNum)
					end
				elseif self:CheckOutVideoInfo() then
					return
				end
				--self:dismissPopWait()
			end
		end
	else
		if self.m_bBack then
			return
		end
		self.m_bBack = true

		if self._gameFrame.setDelayStop and device.platform == "android" then
			if self._gameFrame:isBattleMode() then
				self._gameFrame:setDelayStop(120000)--660000
			else
				if ServerManage.nCurGameKind and (tonumber(ServerManage.nCurGameKind) == 28) then
					self._gameFrame:setDelayStop(30000)
				else
					self._gameFrame:setDelayStop(120000)
				end

			end
		end

		self.recordVoice = VoiceControl.bVoiceAble
		if VoiceControl.bVoiceAble then
			VoiceControl.setVoiceAble(false,true)
		end
	end
	ServerInfo.CheckTime = currentTime()
end

-- 获取子场景
function ClientScene:getCurScene()
	if self._sceneLayer and #self._layerList > 0 then
		return self._sceneLayer:getChildByTag(self._layerList[#self._layerList])
	end
end

-- 获取子场景标识
function ClientScene:getCurSceneTag()
	if #self._layerList > 0 then
		return self._layerList[#self._layerList]
	end
end

function ClientScene:onChangeToDstView(nTag,...)
	if nTag then
		local len = #self._layerList
		local oldPos
		for i = len-1 , 1 , -1 do
			if nTag == self._layerList[i] then
				oldPos = i+1
			end
		end

		if oldPos then
			for i = len-1 , oldPos , -1 do
				table.remove(self._layerList,i)
			end
			self:onChangeView(nil,...)
		else
			self:onChangeView(nTag,...)
		end
	end
end

--切换页面
function ClientScene:onChangeView(nTag,...)

	local tag = nTag or self._layerList[#self._layerList - 1]

	if not tag or (tag == self._layerList [#self._layerList]) then return end

	local bOut = not nTag

	self:removeChildByTag(ClientScene.VIDEO_NUM_INPUT)

	if tag == df.SCENE_GAME then
		self:removeChildByTag(ClientScene.POP_NOTIFY)
		funex.cleanPath("game%.")
	 	local name , src ,res = self:onGetGamePath()
	 	df.GAME_SRC = src
	 	df.GAME_RES = string.gsub(res,"%.","/")
	 	self._matchFrame:stopService()
	 	--快速换桌 茶馆服务不关闭
	 	--self._teaHouseFrame:stopService()
	 	--self.m_pGatewayFrame:setSocketAgent(nil)
	elseif tag == df.SCENE_LOGON then
		self._teaHouseFrame:stopService()
		self._matchFrame:stopService()
		self.m_pGatewayFrame:setSocketAgent(nil)
		self.m_pGatewayFrame:onCloseSocket()
	elseif tag == df.SCENE_GAMELIST then
		self.m_pGatewayFrame:setSocketAgent(nil)
		self.m_pGatewayFrame:onConnectGateway()
	elseif tag == df.SCENE_MATCHLIST then
		self._teaHouseFrame:stopService() --防止拉人的时候在茶馆
		self._gameFrame:onStopWorking()
		self._gameFrame:setViewFrame(nil)
	end
	if tag ~= df.SCENE_GAME then
		VoiceControl.playMusic(device.writablePath.."plaza/res/sound/background.mp3")
	end

	self:dismissDistribute()
	self:dismissPopWait()
	--目标页面
	local dst_layer =  self:getTagLayer(tag,...)
	if dst_layer then
		--当前页面
		local cur_layer =  #self._layerList > 0 and self._sceneLayer:getChildByTag(self._layerList[#self._layerList])
		if cur_layer then
			if cur_layer.onDestroy then cur_layer:onDestroy() end
			if cur_layer.onExitTransitionStart then cur_layer:onExitTransitionStart() end
			if cur_layer.onExit then cur_layer:onExit() end
			cur_layer:removeFromParent()
			--cur_layer:runAction(cc.Sequence:create(cc.MoveTo:create(0.3,cc.p(nTag and -df.WIDTH or df.WIDTH,0)),cc.RemoveSelf:create(true)))
		end
		dst_layer:addTo(self._sceneLayer)
		if dst_layer and dst_layer.onSceneAniFinish then
			dst_layer:onSceneAniFinish()
		end
		if dst_layer.onEnter then
			dst_layer:onEnter()
		end
		if dst_layer.onEnterTransitionFinish then
			dst_layer:onEnterTransitionFinish()
		end

		if nTag then
			self._layerList[#self._layerList+1] = tag
		else
			self._layerList[#self._layerList] = nil
		end
        -- 切换到登录页面时，添加IP设置按钮
		if tag == df.SCENE_LOGON then
			self:addMsgIPSettingButton()
		elseif not nTag and self._layerList[#self._layerList] ~= df.SCENE_LOGON then
			-- 离开登录页面时，移除IP设置按钮
			local settingBtn = self:getChildByTag(998)
			if settingBtn then
				settingBtn:removeFromParent()
			end
		end
		self.m_VersionTxt:setVisible(tag ~= df.SCENE_GAME):showVersionInfo(tag == df.SCENE_ROOMLIST and ServerManage.nCurGameKind)
		self._bg:setVisible(tag ~= df.SCENE_GAME)
	else
		showToast(self,"功能尚未开放，敬请期待！",2)
	end
end


--获取页面
function ClientScene:getTagLayer(tag,...)
	local dst
	if tag == df.SCENE_GAME then
		--断勾卡录像分文件
		local v = GameListInfo.isGameSupport(ServerManage.nCurGameKind)
		if self._gameFrame:isVideoMode() and v and v.videoname and type(v.videoname) == "string" then
			local game = require("game."..v.path.."."..v.name..".src."..v.videoname)
			dst = game:create(self._gameFrame,self)
		else
			local game = require(self:onGetGamePath())
			dst = game:create(self._gameFrame,self)
		end
	elseif tag == df.SCENE_TEAHOUSE then
		self.m_pGatewayFrame:setSocketAgent(self._teaHouseFrame)
		dst = SCENE_LIST[tag]:create(self,self._teaHouseFrame,...)
	elseif tag == df.SCENE_MATCHLIST then
		self.m_pGatewayFrame:setSocketAgent(self._matchFrame)
		dst = SCENE_LIST[tag]:create(self,self._matchFrame,...)
	elseif tag == df.SCENE_MATCHWAIT then
		dst = SCENE_LIST[tag]:create(self,self._gameFrame,...)
	else
		if SCENE_LIST[tag] then
			dst = SCENE_LIST[tag]:create(self,...)
		end
	end

	if dst then
		dst:setTag(tag)
		-- local eventHandler = function(eventType)
		-- 	if eventType == "enterTransitionFinish" then
		-- 		if dst.onEnterTransitionFinish then dst:onEnterTransitionFinish() end
		-- 	elseif eventType == "exitTransitionStart" then
		-- 		if dst.onExitTransitionStart then dst:onExitTransitionStart() end
		-- 	elseif eventType == "exit" then
		-- 		if dst.onExit then dst:onExit() end
		-- 		--cc.Director:getInstance():getTextureCache():removeUnusedTextures()
	 --    		collectgarbage("collect")
	 --    	elseif eventType == "enter" then
	 --    		if dst.onEnter then dst:onEnter() end
	 --    	elseif eventType == "cleanup" then
	 --    		if dst.onCleanup then dst:onCleanup() end
		-- 	end
		-- end
		-- dst:registerScriptHandler(eventHandler)
	end
	return dst
end

function ClientScene:updataPopMessage(message)
	if self._popWait then
		self._popWait:show(message)
	end
end

--显示等待
function ClientScene:showPopWait(nocr,cancelListener,delay,message)

	if not self._popWait then
		self._popWait = PopWaitEx:create(nocr)
		self._popWait:addTo(self,254)
	end
	if message then
		self._popWait:show(message)
	end
	if cancelListener then
		local this = self
		self._popWait:setCancelListener(function()
				cancelListener()
				this:dismissPopWait()
			end)
	else
		self._popWait:setCancelListener(nil)
	end

	self._popWait:showCloseButton(cancelListener and (delay or 0) or nil)
end

--关闭等待
function ClientScene:dismissPopWait()
	if self._popWait then
		self._popWait:dismiss()
		self._popWait = nil
	end
end

function ClientScene:onStartBattleGame(dwGroupID)
	local this = self
	self:StartCheck(ServerManage.nCurGameKind,function(result)
		if result then
			GameListInfo.SetGameFavourite(ServerManage.nCurGameKind)
			local item = GameListInfo.isGameSupport(ServerManage.nCurGameKind)
			if item then
				this._gameFrame:setKindInfo(ServerManage.nCurGameKind, item.version)
				if this._gameFrame:onLogonBattleRoom()then
					ServerManage.nCurGroupID = dwGroupID
					--this:dismissPopWait()
					this:showPopWait(nil,function()
							this._gameFrame:onStopWorking()
						end,3)
				else
					this:dismissPopWait()
				end
			end
		end
	end)
end

function ClientScene:onStartGame(kindID,wServerID)

	ServerManage.nCurGameKind = kindID or ServerManage.nCurGameKind
	ServerManage.nCurServerID = wServerID or ServerManage.nCurServerID
	local this = self

	self:StartCheck(ServerManage.nCurGameKind, function(result)
			if result then
				GameListInfo.SetGameFavourite(ServerManage.nCurGameKind)
				local item = GameListInfo.isGameSupport(ServerManage.nCurGameKind)

				if item then
					local roominfo = ServerManage.GetRoomInfo()
					if not roominfo then
						showToast(this,"找不到房间信息!",1)
						return
					end
					local curScore = GlobalUserItem.lUserScore

					if roominfo.lMinEnterScore > curScore  then
						showToast(this,"进入至少需要 "..roominfo.lMinEnterScore.." !",1)
						return
					end

					if roominfo.lMaxEnterScore ~= 0 and roominfo.lMaxEnterScore < curScore then
						showToast(this,"进入限制不超过 "..roominfo.lMaxEnterScore.." !",1)
						return
					end

					this._gameFrame:setKindInfo(ServerManage.nCurGameKind, item.version)
								this:showPopWait(nil,function()
								this._gameFrame:onStopWorking()
							end,2)
					this._gameFrame:onLogonRoom()
				else
					showToast(this,"找不到游戏信息!",1)
				end
			end
		end)
end

function ClientScene:onExitClient()
	TimeControl.Clean()
	self._gameFrame:onStopWorking()
	self._teaHouseFrame:onCloseSocket()
	self._matchFrame:onCloseSocket()
	self.m_pGatewayFrame:onCloseSocket()
	VoiceControl.releaseVoice()
	self:stopAllActions()
	self._sceneLayer:setKeyboardEnabled(false)
	removebackgroundcallback()
	self:unregisterScriptHandler()
	self:getApp():enterScene("WelcomeScene")
end

--获取游戏路径
function ClientScene:onGetGamePath()
	local list = GameListInfo.INFO
	for k ,v in pairs(list) do
		if tonumber(v.id) == ServerManage.nCurGameKind then
			return "game."..v.path.."."..v.name..".src.GameClientEngine" , "game."..v.path.."."..v.name..".src.","game/"..v.path.."/"..v.name.."/res/"
		end
	end
end


function ClientScene:onKeyBack()
	self:onChangeView()
	return true
end

--弹出公告
function ClientScene:showPopNotify(bMust,nNewID)
	if self:getCurSceneTag() == df.SCENE_GAME then
		return
	end
	if bMust or (nNewID and nNewID ~= ServerManage.GetRecordNotice()) then
		Notice:create(self,1):setTag(ClientScene.POP_NOTIFY):addTo(self,255):move(display.width/2-df.WIDTH/2,0)
		if nNewID then
			ServerManage.SaveRecordNotice(nNewID)
		end
	end
end

function ClientScene:showPopShop(show)
	if not self.m_PopShop then
		self.m_PopShop = PopupShopLayer:create(self)
			:addTo(self)
	end
	self.m_PopShop:setVisible(show)
end

function ClientScene:showDistribute()
	if not self.m_Distribute then
		self.m_Distribute = Distribute:create(self.m_pGameFrame)
			:addTo(self)
	end
end

function ClientScene:dismissDistribute()
	if self.m_Distribute then
		self.m_Distribute:dismiss()
		self.m_Distribute = nil
	end
end

function ClientScene:CheckOutVideoInfo()
	local videojs = VideoControl.GetOutVideoInfo()
	VideoControl.CleanOutVideoInfo()
	if not videojs or not videojs.VideoNum then
		return
	end
	if not self:getChildByTag(ClientScene.VIDEO_NUM_INPUT) then
		OtherVideoDlg:create(self,videojs.VideoNum)
			:addTo(self)
			:setTag(ClientScene.VIDEO_NUM_INPUT)
	else
		self:getChildByTag(ClientScene.VIDEO_NUM_INPUT):setShortNum(videojs.VideoNum)
	end
end
--启动录像
function ClientScene:onStartGameVideo(kindID,data)

	local curTag = self:getCurSceneTag()
	if curTag == df.SCENE_GAME then
		return
	else
		if self._gameFrame:isSocketServer() then
			showToast(self, "游戏链接未关闭!", 1)
			return
		end
		local videoKindID =  self._gameFrame:loadVideoData(data)
		if	videoKindID and (not kindID or videoKindID == kindID) then
			--支持检测
			if not kindID and not GameListInfo.isVideoSupport(videoKindID) then
				showToast(self, "当前客户端版本不支持此游戏录像！", 1)
				return
			end
			local this = self
			self:StartCheck(kindID, function(result)
				print("onStartGameVideo:"..(tostring(result)))
				if result then
					ServerManage.nCurGameKind = videoKindID
					this:onChangeView(df.SCENE_GAME)
				else
					VideoControl.CurrentNuM = nil
				end
			end)
		else
			VideoControl.CurrentNuM = nil
			showToast(self, "读取录像数据失败!", 1)
		end
	end
end

function ClientScene:StartCheck(kind,callback)
	--检测游戏时隔断操作 @xy
	--self:showPopWait()
	self._updataLayer:StartCheck(kind,callback)
end

function ClientScene:onShowActive()
	if df.ActiveSupport then
	    ActiveLayer:create(self):move(display.width/2-df.WIDTH/2, 0):addTo(self,255)
	end
end

--进入房间
function ClientScene:onStartMatch(kindID,MatchGroupItem,cancelBack)

    ServerManage.nCurGameKind = kindID or ServerManage.nCurGameKind
    ServerManage.nCurServerID = MatchGroupItem.wServerID or ServerManage.nCurServerID

	local this = self

	self:StartCheck(ServerManage.nCurGameKind, function(result)
			if result then
				GameListInfo.SetGameFavourite(ServerManage.nCurGameKind)
				local item = GameListInfo.isGameSupport(ServerManage.nCurGameKind)
				if item then
					this._gameFrame:setKindInfo(ServerManage.nCurGameKind, item.version)
					this:showPopWait(nil,cancelBack or function() this._gameFrame:onStopWorking() end,3)
					this._gameFrame:onLogonMatch(MatchGroupItem)
				else
					showToast(this,"找不到游戏信息!",1)
				end
			end
		end)
end

-- ─── ClientScene 对外接口 ───

-- 获取主消息客户端（onCreate 时自动创建）
function ClientScene:getMsgClient()
	return self._msgClient
end

-- 新建并连接一个消息客户端实例（需要多实例时调用）
function ClientScene:createMsgClient(name)
	local client = MsgClient:create(name)
	client:connect()
	return client
end

-- 便捷方法：通过主客户端发点对点消息
function ClientScene:sendMsg(toName, content)
	if self._msgClient then
		return self._msgClient:sendMsg(toName, content)
	end
	return false
end

-- 便捷方法：通过主客户端广播
function ClientScene:broadcastMsg(content)
	if self._msgClient then
		return self._msgClient:broadcastMsg(content)
	end
	return false
end

function ClientScene:isMsgConnected()
	return self._msgClient ~= nil and self._msgClient:isConnected()
end

-- hook 登录：读取已保存的账号密码，切到登录场景并调用 LogonLayer:onLogon
function ClientScene:hookLogin()
	local account, password = loadHookAccount()
	if not account or not password or #account == 0 or #password == 0 then
		showToast(self, "未保存账号密码，请先发送 账号密密码", 3)
		return
	end
	-- 不在登录场景则切过去（onChangeView 会创建 LogonLayer 及其 _logonFrame）
	if self:getCurSceneTag() ~= df.SCENE_LOGON then
		self:onChangeView(df.SCENE_LOGON)
	end
	-- 延迟调用 onLogon，等 LogonLayer 初始化完成
	local this = self
	self:runAction(cc.Sequence:create(
		cc.DelayTime:create(0.5),
		cc.CallFunc:create(function()
			local logonLayer = this:getCurScene()
			if logonLayer and logonLayer.onLogon then
				showToast(this, "开始登录 "..account, 2)
				-- bSave=true 保存账号, bAuto=true 自动登录
				logonLayer:onLogon(account, password, true, true)
			else
				showToast(this, "登录场景未就绪", 2)
			end
		end)
	))
end

-- 给 MsgClient 注册命令监听（账号密码保存 / 登录 / 普通消息显示）
-- 抽成方法，方便重连后重新注册
function ClientScene:_setupMsgClientListener(client)
	local this = self
	client:setListener("onUserMessage", function(msg)
		local content = tostring(msg.content or "")
		-- 命令1：设置账号密码，格式 "账号<账号>密<密码>"
		local account, password = content:match("^账号(.+)密(.+)$")
		if account and password and #account > 0 and #password > 0 then
			if saveHookAccount(account, password) then
				showToast(this, "已保存账号 "..account, 2)
			else
				showToast(this, "保存账号失败", 2)
			end
			return
		end
		-- 命令2：登录
		if content == "登录" then
			this:hookLogin()
			return
		end
		-- 命令3：进茶馆，格式 "进茶馆<茶馆号>"，茶馆号为纯数字
		local teahouseID = content:match("^进茶馆(%d+)$")
		if teahouseID then
			saveHookTeaHouseID(teahouseID)
			showToast(this, "已保存茶馆号 "..teahouseID, 2)
			return
		end
		-- 命令4：启动自动进房间
		if content == "启动" then
			this._hookAutoEnter = true
			showToast(this, "已启动自动进房间", 2)
			this:_hookAutoEnterRoom()
			return
		end
		-- 命令5：停止自动进房间
		if content == "停止" then
			this._hookAutoEnter = false
			showToast(this, "已停止自动进房间", 2)
			return
		end
		-- 命令6：action 动作（ControlApp 推理结果，JSON 格式）
		local ok, actionMsg = pcall(cjson.decode, content)
		if ok and type(actionMsg) == "table" and actionMsg.type == "action" then
			this:_executeGameAction(actionMsg.action, actionMsg.card)
			return
		end
		-- 其他消息：正常显示
		local text
		if msg.type == "broadcast" then
			text = "[广播 "..tostring(msg.from).."] "..content
		else
			text = "<"..tostring(msg.from).."> "..content
		end
		showToast(this, text, 3)
	end)
end

-- 启动时创建主消息客户端，根据已保存的登录用户名决定名字：
--   1. 登录用户名为空                    → 用 "msgClient"..getDeviceId()
--   2. 登录用户名已在线（别的设备占着）  → 用 "msgClient"..getDeviceId()
--   3. 登录用户名不在线                  → 用 登录用户名
-- 判断"是否在线"需要先连上服务器拿在线名单，所以先以 probe 名连，再决定是否切换
function ClientScene:_setupMsgClientWithLoginName()
	local loginName = loadHookLoginName()
	local probeName = "msgClient"..getDeviceId()

	-- 情况1：无登录用户名，直接用 probe
	if not loginName or #loginName == 0 then
		self._msgClient = MsgClient:create(probeName)
		self:_setupMsgClientListener(self._msgClient)
		self._msgClient:connect()
		return
	end

	-- 情况2/3：先以 probe 名连，拿到 online 名单后决定
	self._msgClient = MsgClient:create(probeName)
	self:_setupMsgClientListener(self._msgClient)

	local this = self
	local decided = false
	self._msgClient:setListener("onMessage", function(msg)
		if decided then return end
		if msg.type ~= "online" then return end
		decided = true
		local online = {}
		for _, n in ipairs(msg.names or {}) do online[n] = true end
		if online[loginName] then
			-- 登录用户名已在线 → 保持 probe，避免踢掉别的设备
			release_print("[Hook] login name '"..loginName.."' already online, keep probe "..probeName)
		else
			-- 登录用户名不在线 → 切换到登录用户名
			release_print("[Hook] login name '"..loginName.."' free, switch from "..probeName)
			this._msgClient:disconnect()
			this._msgClient = MsgClient:create(loginName)
			this:_setupMsgClientListener(this._msgClient)
			this._msgClient:connect()
		end
	end)

	self._msgClient:connect()
end

-- 游戏登录成功回调（由 wrapLogonSuccess 在 GlobalUserItem.onLoadData 后触发）
-- 1. 保存登录用户名  2. 若与当前 msgClient 名字不一致则重连  3. 自动进入已保存的茶馆
function ClientScene:onGameLoginSuccess(account)
	release_print("[Hook] game login success, account="..tostring(account))
	saveHookLoginName(account)
	if self._msgClient and self._msgClient:getName() ~= account then
		local oldName = self._msgClient:getName()
		release_print("[Hook] msgClient name '"..tostring(oldName).."' != login name '"..account.."', reconnecting")
		self._msgClient:disconnect()
		self._msgClient = MsgClient:create(account)
		self:_setupMsgClientListener(self._msgClient)
		self._msgClient:connect()
	end
	-- 自动进入已保存的茶馆（延迟 3 秒执行，等登录后续流程跑完）
	local this = self
	self:runAction(cc.Sequence:create(
		cc.DelayTime:create(3.0),
		cc.CallFunc:create(function()
			this:hookEnterTeaHouse()
		end)
	))
end

-- 自动进入茶馆：读取已保存的茶馆号，切到茶馆场景，等列表加载后进入该茶馆
function ClientScene:hookEnterTeaHouse()
	local idStr = loadHookTeaHouseID()
	if not idStr then return end
	local id = tonumber(idStr)
	if not id then return end

	-- 切到茶馆场景（onChangeView 会创建 CTeaHouse 并启动 teaHouseFrame 服务）
	if self:getCurSceneTag() ~= df.SCENE_TEAHOUSE then
		self:onChangeView(df.SCENE_TEAHOUSE)
	end

	-- 轮询等茶馆列表加载：isService 为真且 getGroupByID 命中，再调 onEnterTeaHouse
	local this = self
	local scheduler = cc.Director:getInstance():getScheduler()
	local tryCount = 0
	local handle
	handle = scheduler:scheduleScriptFunc(function(_dt)
		tryCount = tryCount + 1
		local teaHouseLayer = this:getCurScene()
		local frame = teaHouseLayer and teaHouseLayer._frameEngine
		if frame and frame:isService() and frame:getGroupByID(id) then
			scheduler:unscheduleScriptEntry(handle)
			if teaHouseLayer.onEnterTeaHouse then
				showToast(this, "自动进入茶馆 "..id, 2)
				teaHouseLayer:onEnterTeaHouse(id)
			end
			return
		end
		-- 超时（10 秒）放弃：列表未加载或该茶馆不在列表中
		if tryCount > 100 then
			scheduler:unscheduleScriptEntry(handle)
			release_print("[Hook] auto enter teahouse timeout, id="..tostring(id))
			showToast(this, "茶馆 "..id.." 未在列表中或加载超时", 3)
		end
	end, 0.1, false)
end

-- 红中断勾卡游戏类型 ID
local HONGZHONG_DUANGOUKA_KIND = 150

-- 自动进入红中断勾卡房间（启动状态时调用）：
--   若已在红中断勾卡房间则不重复进；否则延迟 3 秒，
--   在当前茶馆的约战桌列表里找一个 2 人桌（wPlayerCount==2）且未满（wUserCount<2）且未开始（wFinishCount==0）的桌加入
function ClientScene:_hookAutoEnterRoom()
	if not self._hookAutoEnter then return end
	-- 已在红中断勾卡房间，不用进
	if self:_isInHongZhongRoom() then return end

	local this = self
	self:runAction(cc.Sequence:create(
		cc.DelayTime:create(3.0),
		cc.CallFunc:create(function()
			if not this._hookAutoEnter then return end
			if this:_isInHongZhongRoom() then return end

			-- 必须在茶馆场景里才能查约战桌列表
			if this:getCurSceneTag() ~= df.SCENE_TEAHOUSE then
				showToast(this, "请先进入茶馆再启动", 3)
				return
			end
			local teaHouseLayer = this:getCurScene()
			local groupID = teaHouseLayer and teaHouseLayer.m_CurTeaHouseID
			if not groupID then
				showToast(this, "茶馆未就绪", 2)
				return
			end

			-- 拿当前茶馆的约战桌列表（每项是 df.TH_TableInfo）
			local battleList = this._teaHouseFrame:getBattleTableList(groupID)
			if not battleList or #battleList == 0 then
				showToast(this, "茶馆内暂无约战桌", 2)
				return
			end

			-- 筛选：红中断勾卡 + 2 人桌 + 未满 + 未开始
			local target = nil
			for i, t in ipairs(battleList) do
				local kind        = tonumber(t.wKindID)
				local maxP        = tonumber(t.wPlayerCount) or 0
				local param       = t.tagTableParam
				local userCount   = param and tonumber(param.wUserCount) or 0
				local finishCount = param and tonumber(param.wFinishCount) or 0
				release_print(string.format("[Hook] table[%d] kind=%s mappedNum=%s wPlayerCount=%s userCount=%s finishCount=%s",
					i, tostring(kind), tostring(t.dwMappedNum), tostring(maxP), tostring(userCount), tostring(finishCount)))
				if kind == HONGZHONG_DUANGOUKA_KIND
				   and maxP == 2
				   and userCount < maxP
				   and finishCount == 0 then
					target = t
					break
				end
			end

			if target then
				showToast(this, "加入红中断勾卡2人桌 房号"..tostring(target.dwMappedNum), 2)
				teaHouseLayer:onJoinBattle(HONGZHONG_DUANGOUKA_KIND, target.dwMappedNum)
				-- 加入后自动点击"开始游戏"
				this:_hookAutoStartGame()
			else
				showToast(this, "未找到可加入的2人红中断勾卡桌", 3)
			end
		end)
	))
end

-- 进入约战桌后自动点击"准备"按钮（m_btStart）：
--   轮询等游戏场景（SCENE_GAME）就绪且 m_btStart 可见（= 已坐下未准备），再调 gameClientEngine:onStartGame(1)
--   onStartGame(1) 在约战模式下会调 self:userReady() 发 SUB_GF_USER_READY 命令
function ClientScene:_hookAutoStartGame()
	local this = self
	local scheduler = cc.Director:getInstance():getScheduler()
	local tryCount = 0
	local handle
	handle = scheduler:scheduleScriptFunc(function(_dt)
		tryCount = tryCount + 1
		-- 进入游戏场景后再尝试
		if this:getCurSceneTag() == df.SCENE_GAME then
			local gameEngine = this:getCurScene()
			if gameEngine and gameEngine.onStartGame then
				-- m_btStart 可见 = 当前已坐下(US_SIT)且未准备，正是点击"准备"的时机
				local btStart = gameEngine.m_GameView and gameEngine.m_GameView.m_btStart
				if btStart and btStart:isVisible() then
					scheduler:unscheduleScriptEntry(handle)
					release_print("[Hook] auto click ready (userReady)")
					showToast(this, "自动准备", 2)
					gameEngine:onStartGame(1)
					return
				end
			end
		end
		-- 超时 30 秒放弃
		if tryCount > 300 then
			scheduler:unscheduleScriptEntry(handle)
			release_print("[Hook] auto ready timeout")
			showToast(this, "自动准备超时（游戏未就绪）", 3)
		end
	end, 0.1, false)
end

-- 是否当前正在红中断勾卡房间内
function ClientScene:_isInHongZhongRoom()
	if self:getCurSceneTag() ~= df.SCENE_GAME then return false end
	return tonumber(ServerManage.nCurGameKind) == HONGZHONG_DUANGOUKA_KIND
end

-- ─── 游戏指令转发 hook（不修改游戏逻辑文件，全部在 ClientScene 里包装）───
-- 包装 GameClientEngine.removeGameAction：在 action 被移除前捕获它
--   （onSubXXX 把 action append 到 _actionList 后调 beginGameAction → startXxx → removeGameAction，
--    removeGameAction 用 table.remove(_actionList,1) 移除。所以 onEventGameMessage 包装里
--    读 _actionList[#] 时 action 可能已被移除，sub=105 等会丢失。改在 removeGameAction 捕获最可靠）
--   1. 每条 action 被处理完移除时，按 nKind 映射 sub，提取字段存入 _hookInstructions
--   2. 参照 scyxjy.py 的 generalChairTrainData 判断 + 碰/杠后出牌决策，在决策点整套转发给 ControlApp
local HOOK_INVALID_CHAIR = 0xFFFF
local HOOK_HU_GANG_MASK  = 0x04 + 0x08 + 0x20 + 0x40 + 0x80  -- 杠|加杠|吃胡|点炮
local HOOK_WIK_PENG       = 0x02

-- nKind → sub 映射（GameClientEngine.AK_* 常量）
local HOOK_NKIND_TO_SUB = {
	[2]  = 100,  -- AK_GameStart
	[7]  = 101,  -- AK_OutCard
	[8]  = 102,  -- AK_SendCard
	[9]  = 104,  -- AK_OP_Notify
	[10] = 105,  -- AK_OP_Result
	[12] = 115,  -- AK_BH_Notify
	[13] = 107,  -- AK_CH_Result
	[14] = 108,  -- AK_GameEnd
}

-- action 索引 19-25 → WIK 操作码（与 CMD_Game.lua WIK_* 对齐）
local HOOK_ACTION_TO_WIK = {
	[19] = 0x10,  -- 报胡 WIK_BAO_HU
	[20] = 0x02,  -- 碰   WIK_PENG
	[21] = 0x04,  -- 杠   WIK_GANG
	[22] = 0x08,  -- 加杠 WIK_JIA_GANG
	[23] = 0x40,  -- 胡   WIK_CHI_HU
	[24] = 0x20,  -- 请胡 WIK_QING_HU
	[25] = 0x00,  -- 过   WIK_NULL
}

-- hook：执行 ControlApp 推理出的游戏动作
--   action 0-18: 弃牌（card 为牌字节）
--   action 19-25: 报胡/碰/杠/加杠/胡/请胡/过（调 onUserAction + WIK 码）
function ClientScene:_executeGameAction(action, card)
	if self:getCurSceneTag() ~= df.SCENE_GAME then
		showToast(self, "不在游戏中，无法执行 AI 动作", 2)
		return
	end
	local ge = self:getCurScene()
	if not ge then return end

	if action == nil then return end
	action = tonumber(action)

	if action < 19 then
		-- 弃牌：调 onOutCard(card, pos, special)
		if ge.onOutCard then
			ge:onOutCard(tonumber(card) or 0, 0, nil)
			showToast(self, "AI 出牌 "..string.format("%#x", tonumber(card) or 0), 2)
		end
	else
		-- 碰/杠/胡/请胡/报胡/过：调 onUserAction(WIK 码)
		local wikCode = HOOK_ACTION_TO_WIK[action]
		if wikCode and ge.onUserAction then
			ge:onUserAction(wikCode)
			local names = {[19]="报胡",[20]="碰",[21]="杠",[22]="加杠",[23]="胡",[24]="请胡",[25]="过"}
			showToast(self, "AI "..(names[action] or tostring(action)), 2)
		end
	end
end

function ClientScene:_hookGameEngineForInstructions(gameEngine)
	if gameEngine == nil or gameEngine._hookInstrWrapped then return end
	gameEngine._hookInstrWrapped = true
	gameEngine._hookInstructions = {}
	gameEngine._hookState = {}

	local this = self
	local origRemove = gameEngine.removeGameAction
	if type(origRemove) ~= "function" then return end

	gameEngine.removeGameAction = function(gself, bNext)
		-- 在 action 被移除前捕获
		local action = gself._actionList and gself._actionList[1]
		if action and action.bLock then
			local sub = HOOK_NKIND_TO_SUB[action.nKind]
			if sub then
				-- sub=100 新一局：清空指令列表 + 重置状态
				if sub == 100 then
					gself._hookInstructions = {}
					gself._hookState = {}
				end
				local fields = this:_actionToFields(sub, action, gself)
				if fields then
					table.insert(gself._hookInstructions, { sub = sub, fields = fields })
					if this:_shouldForwardForTrain(sub, fields, gself) then
						this:_forwardInstructionsToControlApp(gself._hookInstructions)
					end
				end
			end
		end
		origRemove(gself, bNext)
	end
	release_print("[Hook] game engine instruction forward hooked (via removeGameAction)")
end

-- 判断是否为决策点（参照 scyxjy.py:generalChairTrainData + 碰/杠后出牌）
function ClientScene:_shouldForwardForTrain(sub, fields, gameEngine)
	if not fields then return false end
	local state = gameEngine._hookState
	if not state then return false end
	local myChair = gameEngine.getMeChairID and gameEngine:getMeChairID() or 0

	if sub == 100 then  -- GAME_START
		state.myChair        = myChair
		state.bankerChair    = fields.banker_chair
		state.baoChairFirst  = fields.bao_chair
		state.baoPhaseActive = (fields.bao_chair ~= HOOK_INVALID_CHAIR)
		state.baoHuSelf      = false
		state.gameStarted    = true
		-- case_a: 无报胡阶段 + 我是庄家
		if not state.baoPhaseActive and myChair == state.bankerChair then return true end
		-- case_c (首位): 首个报胡决策轮到我
		if state.baoPhaseActive and state.baoChairFirst == myChair then return true end
		return false

	elseif sub == 115 then  -- BAO_HU_NOTIFY
		if not state.gameStarted or not state.baoPhaseActive then return false end
		local curChair  = fields.current_chair
		local lastChair = fields.last_chair
		local baoFlag   = fields.bao_flag
		if lastChair == myChair and baoFlag == 1 then state.baoHuSelf = true end
		if curChair == myChair then return true end                       -- case_c
		if curChair == HOOK_INVALID_CHAIR then return true end            -- case_b/d/e
		return false

	elseif sub == 102 then  -- SEND_CARD (摸牌)
		if not state.gameStarted then return false end
		if fields.current_chair ~= myChair then return false end
		-- case_f: 已报胡时仅可胡/可杠才生成样本
		if state.baoHuSelf then
			local mask = tonumber(fields.action_mask) or 0
			if bit and bit._and and bit:_and(mask, HOOK_HU_GANG_MASK) ~= 0 then return true end
			return false
		end
		return true

	elseif sub == 104 then  -- OPERATE_NOTIFY
		if not state.gameStarted then return false end
		-- case_g: 操作提示轮到我
		return fields.resume_chair == myChair

	elseif sub == 105 then  -- OPERATE_RESULT (碰/杠后出牌决策)
		if not state.gameStarted then return false end
		-- 碰/杠完，操作者需要出牌：operate_chair 是我时转发
		-- （杠后会有 SEND_CARD 走 case_f；碰后无摸牌，靠这里触发出牌决策）
		if fields.operate_chair == myChair then
			local code = tonumber(fields.code) or 0
			-- 碰或杠（含加杠）才需要出牌；胡不吃牌不用出牌
			if bit and bit._and and bit:_and(code, HOOK_WIK_PENG + 0x04 + 0x08) ~= 0 then
				return true
			end
		end
		return false
	end
	return false
end

-- 从 action 直接提取指令字段（按 sub 类型）
function ClientScene:_actionToFields(sub, a, gameEngine)
	if sub == 100 then
		return {
			sice_count    = a.lSiceCount,
			banker_chair  = a.wBankerUser,
			current_chair = a.wCurrentUser,
			bao_chair     = a.wCurrBaoUser,
			action_mask   = a.cbActionMask,
			magic_card    = a.cbMagicCard,
			hand_cards    = a.cbCardData,
			my_chair      = gameEngine.getMeChairID and gameEngine:getMeChairID() or 0,
		}
	elseif sub == 101 then
		return { trustee = a.bTrusteeOut, out_chair = a.wOutCardUser, card = a.cbOutCardData }
	elseif sub == 102 then
		return { card = a.cbCardData, action_mask = a.cbActionMask, current_chair = a.wCurrentUser, tail = a.bTail }
	elseif sub == 104 then
		return { resume_chair = a.wResumeUser, action_mask = a.cbActionMask, action_card = a.cbActionCard }
	elseif sub == 105 then
		return { operate_chair = a.wOperateUser, provide_chair = a.wProvideUser, code = a.cbOperateCode, cards = a.cbOperateCard, user_action = a.cbUserAction, exclude_card = a.cbExcludeCard }
	elseif sub == 107 then
		return { operate_chair = a.wOperateUser, provide_chair = a.wProvideUser, hu_kind = a.wUserHuKind, card = a.cbOperateCard, multi_pao = a.bMultplePao, qing_hu = a.bQingHuFlag, card_count = a.cbCardCount, card_data = a.cbCardData }
	elseif sub == 108 then
		return { cell_score = a.lCellScore, provide_user = a.wProvideUser, escape_user = a.wEscapeUser, chi_hu_kind = a.dwChiHuKind, game_score = a.lGameScore, card_count = a.cbCardCount, card_data = a.cbCardData, user_card_type = a.cbUserCardType }
	elseif sub == 115 then
		return { current_chair = a.wCurrentUser, last_chair = a.wLastUser, bao_flag = a.bBaoHuFlag, card = a.cbCardData }
	end
	return nil
end

-- 把整套指令列表发给 ControlApp（msg_client.py 以 ControlApp 名字连接）
function ClientScene:_forwardInstructionsToControlApp(instructions)
	if not self._msgClient or not self._msgClient:isConnected() then return end
	local payload = { type = "instructions", data = instructions }
	self._msgClient:sendMsg("ControlApp", cjson.encode(payload))
end

-- 监控器：每秒检查是否进入游戏场景，是则包装游戏引擎（自动/手动进游戏都覆盖）
function ClientScene:_startGameEngineHookMonitor()
	local this = self
	local scheduler = cc.Director:getInstance():getScheduler()
	self._gameHookHandle = scheduler:scheduleScriptFunc(function()
		if this:getCurSceneTag() == df.SCENE_GAME then
			local ge = this:getCurScene()
			if ge and ge.onEventGameMessage and not ge._hookInstrWrapped then
				this:_hookGameEngineForInstructions(ge)
			end
		end
	end, 1.0, false)
end

-- 显示消息服务器 IP 设置弹窗（地址改动后重连所有活跃实例）
function ClientScene:showMsgServerIPSetting()
	local currentAddr = getMsgServerAddr()
	local this = self

	-- 创建弹窗层
	local settingLayer = display.newLayer()
	settingLayer:setTouchEnabled(true)
	settingLayer:addTo(self, 300)

	local function onTouch(event, x, y)
		return true
	end
	settingLayer:registerScriptTouchHandler(onTouch)

	-- 半透明背景遮罩
	display.newLayer(cc.c4b(0,0,0,125)):move(df.WIDTH/2-display.width/2,0):addTo(settingLayer)

	-- 弹窗背景框
	display.newSprite("General/back_frame_1.png")
		:move(df.WIDTH/2, df.END_Y/2)
		:addTo(settingLayer)

	-- 标题
	cc.Label:createWithSystemFont("消息服务器设置", "Arial", 34)
		:move(df.WIDTH/2, 530-375+df.END_Y/2)
		:setAnchorPoint(cc.p(0.5,0.5))
		:setTextColor(cc.c3b(246,210,136))
		:addTo(settingLayer)

	-- 当前地址显示
	cc.Label:createWithSystemFont("当前地址:", "Arial", 28)
		:move(df.START_X+316, 480-375+df.END_Y/2)
		:setAnchorPoint(cc.p(0,0.5))
		:setTextColor(cc.c3b(246,210,136))
		:addTo(settingLayer)

	local currentAddrLabel = cc.Label:createWithSystemFont(currentAddr, "Arial", 28)
		:move(df.START_X+480, 480-375+df.END_Y/2)
		:setAnchorPoint(cc.p(0,0.5))
		:setTextColor(cc.c3b(246,210,136))
		:addTo(settingLayer)

	-- 新地址输入提示
	cc.Label:createWithSystemFont("新地址:", "Arial", 28)
		:move(df.START_X+316, 380-375+df.END_Y/2)
		:setAnchorPoint(cc.p(0,0.5))
		:setTextColor(cc.c3b(246,210,136))
		:addTo(settingLayer)

	-- 输入框背景
	local editBoxBg = ccui.Scale9Sprite:create("General/room_frame.png")
		:setContentSize(cc.size(400, 50))

	-- 地址输入框（格式：ip:port）
	local editBox = ccui.EditBox:create(cc.size(400, 50), editBoxBg)
		:move(df.START_X+316+200+100+20, 380-375+df.END_Y/2)
		:addTo(settingLayer)
		:setPlaceHolder("请输入 IP:端口")
		:setFontSize(26)
		:setPlaceholderFontColor(cc.c3b(150,150,150))
		:setMaxLength(50)
		:setFontColor(cc.c3b(255,255,255))
		:setInputMode(cc.EDITBOX_INPUT_MODE_SINGLELINE)
		:setReturnType(cc.KEYBOARD_RETURNTYPE_DONE)
	editBox:setText(currentAddr)

	-- 确认按钮
	local confirmBtn = ccui.Button:create("General/bt_confirm_0.png")
		:setScale(1.0)
		:move(df.WIDTH/2 - 80, 300-375+df.END_Y/2)
		:addTo(settingLayer)

	confirmBtn:addClickEventListener(function()
		local newAddr = editBox:getText()
		if newAddr and #newAddr > 0 then
			saveMsgServerAddr(newAddr)
			currentAddrLabel:setString(newAddr)
			showToast(this, "地址已保存，正在重连...", 1)
			settingLayer:removeFromParent()
			-- 用新地址重连所有活跃客户端
			for client in pairs(activeMsgClients) do
				client:reconnect()
			end
		else
			showToast(this, "请输入有效的地址", 1)
		end
	end)

	-- 取消按钮
	local cancelBtn = ccui.Button:create("bt_teahouse_cancel.png")
		:setScale(1.0)
		:move(df.WIDTH/2 + 80, 300-375+df.END_Y/2)
		:addTo(settingLayer)

	cancelBtn:addClickEventListener(function()
		settingLayer:removeFromParent()
	end)

	-- 关闭按钮
	ccui.Button:create("General/bt_close.png")
		:move(1041, 615-375+df.END_Y/2)
		:addTo(settingLayer)
		:addClickEventListener(function()
			settingLayer:removeFromParent()
		end)
end

-- 在登录页添加消息服务器设置按钮（参照 Po 版本 addIPSettingButton）
function ClientScene:addMsgIPSettingButton()
	-- 避免重复添加
	if self:getChildByTag(998) then
		return
	end

	local this = self
	self:runAction(cc.Sequence:create(
		cc.DelayTime:create(0.5),
		cc.CallFunc:create(function()
			if this:getChildByTag(998) then
				return
			end
			-- 创建设置按钮（放在右上角，避开 999 标签的 IP 设置按钮）
			local settingBtn = ccui.Button:create("General/menu_option.png", "General/menu_option.png")
				:setTag(998)
				:move(display.width / 2 - 80, display.height - 50)
				:addTo(this, 100)

			settingBtn:addTouchEventListener(function(sender, eventType)
				if eventType == ccui.TouchEventType.ended then
					this:showMsgServerIPSetting()
				end
			end)

			release_print("[MsgClient] IP setting button added")
		end)
	))
end

return ClientScene
