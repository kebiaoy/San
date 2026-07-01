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


-- ─── MsgClient 类 ───

local MsgClient = class("MsgClient")

function MsgClient:ctor(name)
	self._name             = name
	self._ws               = nil
	self._reconnectHandler = nil
	self._registered       = false
	self._listeners        = {}  -- onOpen / onMessage / onClose / onError
end

function MsgClient:getName()
	return self._name
end

function MsgClient:isConnected()
	return self._registered and self._ws ~= nil
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
		local names = msg.names or {}
		release_print("[MsgClient:"..tostring(self._name).."] online: "..table.concat(names, ","))
	elseif t == "presence" then
		release_print("[MsgClient:"..tostring(self._name).."] "..tostring(msg.name).." "..tostring(msg.event))
	elseif t == "msg" then
		release_print("[MsgClient:"..tostring(self._name).."] <"..tostring(msg.from).."> "..tostring(msg.content))
	elseif t == "broadcast" then
		release_print("[MsgClient:"..tostring(self._name).."] [broadcast "..tostring(msg.from).."] "..tostring(msg.content))
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


    -- 创建并连接主消息客户端（名字 = msgClient + 设备唯一ID）
	self._msgClient = MsgClient:create("msgClient"..getDeviceId())
	self._msgClient:connect()

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
