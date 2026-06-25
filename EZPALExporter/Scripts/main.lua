-- EZPALExporter - Palworld player and pal data exporter for EZPAL
-- Hooks into UE4SS to extract live player/pal data and write JSON files.

local config = require("config")

local EZPAL = {
    output_dir = config.output_dir,
    tick_interval = config.tick_interval or 5,
    max_pals = config.max_pals_output or 0,
    version = "1.0.0",
}

function EZPAL:log(msg)
    print("[EZPAL] " .. msg)
end

-- Safe property read: returns value or nil on failure
function EZPAL:get(obj, prop)
    if not obj then return nil end
    local ok, val = pcall(function() return obj:GetPropertyValue(prop) end)
    if ok then return val end
    return nil
end

-- Convert a value to a JSON-safe Lua type recursively
function EZPAL:to_lua(val, depth)
    depth = depth or 0
    if depth > 20 then return tostring(val) end

    if val == nil then
        return nil
    end

    local t = type(val)

    -- Primitives pass through
    if t == "string" or t == "number" or t == "boolean" then
        return val
    end

    -- UE Objects need recursive handling
    if t == "userdata" then
        -- Try to get properties if it's a UE object
        local name = ""
        local ok, fn = pcall(function() return val:GetFullName() end)
        if ok then name = fn end

        -- Check if it's an array-like (TArray) by trying Num() or Get()
        local has_num, num_val = pcall(function() return val:Num() end)
        if has_num and type(num_val) == "number" then
            local result = {}
            for i = 0, num_val - 1 do
                local ok_item, item = pcall(function() return val:Get(i) end)
                if ok_item then
                    result[i + 1] = self:to_lua(item, depth + 1)
                end
            end
            return result
        end

        -- Check if it's a map-like (TMap) by trying Num() and GetKeys()
        local has_keys, keys = pcall(function()
            local k = val:GetKeys()
            return k
        end)
        if has_keys and keys then
            local result = {}
            local key_list = self:to_lua(keys, depth + 1)
            if type(key_list) == "table" then
                for _, key in ipairs(key_list) do
                    local ok_item, item = pcall(function() return val:Get(key) end)
                    if ok_item then
                        result[tostring(key)] = self:to_lua(item, depth + 1)
                    end
                end
            end
            return result
        end

        -- Struct-like (FGuid, FUniqueNetId, etc.) - try tostring
        local ok_str, str_val = pcall(function() return tostring(val) end)
        if ok_str then
            return str_val
        end

        return name
    end

    -- Lua table: recurse with cap
    if t == "table" then
        local is_array = true
        for k in pairs(val) do
            if type(k) ~= "number" or k ~= math.floor(k) or k < 1 then
                is_array = false
                break
            end
        end

        if is_array and #val > 0 then
            local result = {}
            for i = 1, #val do
                result[i] = self:to_lua(val[i], depth + 1)
            end
            return result
        else
            local result = {}
            for k, v in pairs(val) do
                result[tostring(k)] = self:to_lua(v, depth + 1)
            end
            return result
        end
    end

    return tostring(val)
end

-- Extract pal data from a single pal handle/parameter component
function EZPAL:parse_pal(handle_or_param)
    local param = handle_or_param

    -- If it's a handle, try to get its parameter component
    local is_handle = false
    local full_name = ""
    local ok_name, fn = pcall(function() return handle_or_param:GetFullName() end)
    if ok_name then
        full_name = fn
        if full_name:find("IndividualCharacterHandle") then
            is_handle = true
        end
    end

    if is_handle then
        -- Try common parameter component property names
        param = self:get(handle_or_param, "ParameterComponent")
            or self:get(handle_or_param, "IndividualParameter")
            or self:get(handle_or_param, "CharacterParameter")
            or self:get(handle_or_param, "Param")
    end

    if not param then
        return nil
    end

    -- Extract pal properties
    local char_id = self:get(param, "CharacterID")
        or self:get(param, "character_id")
        or self:get(param, "CharacterId")
        or ""

    -- Convert FName/FString to plain string
    if type(char_id) ~= "string" then
        char_id = tostring(char_id)
    end

    if char_id == "" or char_id == "None" then
        return nil
    end

    local pal = {
        character_id = char_id,
        display_name = char_id:gsub("_", " "):gsub("(%l)(%u)", "%1 %2"),
        level = tonumber(self:get(param, "Level") or self:get(param, "level") or 1) or 1,
        rank = tonumber(self:get(param, "Rank") or self:get(param, "rank") or 0) or 0,
        talent_hp = tonumber(self:get(param, "TalentHP") or self:get(param, "talent_hp") or 0) or 0,
        talent_attack = tonumber(self:get(param, "TalentAttack") or self:get(param, "talent_attack") or 0) or 0,
        talent_defense = tonumber(self:get(param, "TalentDefense") or self:get(param, "talent_defense") or 0) or 0,
        is_boss = (self:get(param, "IsBoss") or self:get(param, "is_boss") or false) == true,
        nickname = self:get(param, "Nickname") or self:get(param, "nickname") or "",
        passive_skills = {},
        gender = self:get(param, "Gender") or self:get(param, "gender") or "",
    }

    -- Try to get passives as array
    local passives = self:get(param, "PassiveSkill")
        or self:get(param, "passive_skills")
        or self:get(param, "PassiveSkills")
    if passives then
        local p_list = self:to_lua(passives, 0)
        if type(p_list) == "table" then
            pal.passive_skills = p_list
        elseif type(p_list) == "string" then
            pal.passive_skills = {p_list}
        end
    end

    return pal
end

-- Extract pals from a container object
function EZPAL:parse_container(container)
    local pals = {}
    if not container then return pals end

    -- Try different property names for the list of handles
    local handles = self:get(container, "IndividualHandle")
        or self:get(container, "individual_handle")
        or self:get(container, "Handles")
        or self:get(container, "handles")
        or container  -- Try the container itself as the array

    local handle_list = self:to_lua(handles, 0)

    if type(handle_list) == "table" then
        for _, handle in ipairs(handle_list) do
            -- handle might be a userdata (UE4 object) or already converted table
            if type(handle) == "userdata" then
                local pal = self:parse_pal(handle)
                if pal then
                    table.insert(pals, pal)
                end
            elseif type(handle) == "table" and handle.character_id then
                table.insert(pals, handle)
            end
        end
    end

    return pals
end

-- Parse a single player state and extract all pal data
function EZPAL:parse_player(player_state)
    if not player_state then return nil end

    -- Get player UID - try multiple property names
    local uid = self:get(player_state, "PlayerUId")
        or self:get(player_state, "UID")
        or self:get(player_state, "PlayerId")

    if not uid then
        return nil
    end

    local steam_id = tostring(uid)
    -- Normalize: remove braces, spaces, clean it up
    steam_id = steam_id:gsub("[{}%- ]", "")
    -- If it's a 32-byte hex string (FGuid), lowercase it
    if #steam_id == 32 then
        steam_id = steam_id:lower()
    end

    local player = {
        steam_id = steam_id,
        player_name = self:get(player_state, "PlayerName")
            or self:get(player_state, "player_name") or "",
        nickname = self:get(player_state, "NickName")
            or self:get(player_state, "nickname") or "",
        pals = {},
        party = {},
    }

    -- Extract pals from containers
    local otomo = self:get(player_state, "OtomoCharacterContainer")
        or self:get(player_state, "otomo_character_container")
    if otomo then
        player.party = self:parse_container(otomo)
    end

    local storage = self:get(player_state, "PalStorage")
        or self:get(player_state, "pal_storage")
        or self:get(player_state, "Storage")
    if storage then
        local stored_pals = self:parse_container(storage)
        for _, p in ipairs(stored_pals) do
            table.insert(player.pals, p)
        end
    end

    -- If party pals aren't already in pals list, add them
    local seen = {}
    for _, p in ipairs(player.pals) do
        seen[p.character_id .. ":" .. tostring(p.level) .. ":" .. tostring(p.rank)] = true
    end
    for _, p in ipairs(player.party) do
        local key = p.character_id .. ":" .. tostring(p.level) .. ":" .. tostring(p.rank)
        if not seen[key] then
            table.insert(player.pals, p)
            seen[key] = true
        end
    end

    -- Truncate pal list if configured
    if self.max_pals > 0 and #player.pals > self.max_pals then
        player.pals_truncated = #player.pals
        local tmp = {}
        for i = 1, self.max_pals do
            tmp[i] = player.pals[i]
        end
        player.pals = tmp
    end

    return player
end

-- Scan all connected players
function EZPAL:scan_players()
    -- Find the game state on the server
    local gs = FindFirstOf("BP_PalGameStateInGame_C")
    if not gs then
        gs = FindFirstOf("PalGameStateInGame")
    end
    if not gs then
        gs = FindFirstOf("BP_PalGameState_C")
    end
    if not gs then
        return {}
    end

    local player_array = self:get(gs, "PlayerArray")
    if not player_array then
        return {}
    end

    -- Convert to Lua table and iterate
    local arr = self:to_lua(player_array, 0)
    if type(arr) ~= "table" then
        return {}
    end

    local players = {}
    for _, ps in ipairs(arr) do
        -- ps might be userdata (UE4 APlayerState) or already a table
        local player = nil
        if type(ps) == "userdata" then
            player = self:parse_player(ps)
        elseif type(ps) == "table" and ps.steam_id then
            player = ps
        end

        if player and player.steam_id and #player.steam_id > 0 then
            players[player.steam_id] = player
        end
    end

    return players
end

-- Write JSON to a file
function EZPAL:write_json(filename, data)
    local path = self.output_dir .. "/" .. filename
    local ok, encoded = pcall(function() return json.encode_pretty(data) end)
    if not ok then
        ok, encoded = pcall(function() return json.encode(data) end)
    end
    if not ok then
        self:log("Failed to encode JSON for " .. filename)
        return false
    end

    -- Write to temp file first, then atomically rename
    local tmp = path .. ".tmp"
    local f, err = io.open(tmp, "w")
    if not f then
        self:log("Failed to open " .. tmp .. " for writing: " .. tostring(err))
        return false
    end
    f:write(encoded)
    f:close()

    -- Atomic rename (works on both Windows and Linux)
    os.rename(tmp, path)
    return true
end

-- Write all player files
function EZPAL:write_files(players)
    local count = 0
    for sid, p in pairs(players) do
        -- Write per-player file
        self:write_json(sid .. ".json", p)
        count = count + 1
    end

    -- Write combined file
    local combined = {}
    for _, p in pairs(players) do
        table.insert(combined, p)
    end

    -- Sort by player_name for consistency
    table.sort(combined, function(a, b)
        return (a.player_name or a.steam_id) < (b.player_name or b.steam_id)
    end)

    local meta = {
        last_updated = os.date("!%Y-%m-%dT%H:%M:%SZ"),
        player_count = count,
        players = combined,
    }

    self:write_json("all_players.json", meta)

    if count > 0 then
        self:log("Exported " .. count .. " player(s) to " .. self.output_dir)
    end
end

-- Main tick function
function EZPAL:tick()
    local ok, err = pcall(function()
        local players = self:scan_players()
        if players and next(players) then
            self:write_files(players)
        end
    end)
    if not ok then
        self:log("Tick error: " .. tostring(err))
    end
end

-- Initialization
EZPAL:log("EZPALExporter v" .. EZPAL.version .. " loaded.")
EZPAL:log("Output dir: " .. EZPAL.output_dir)
EZPAL:log("Tick interval: " .. EZPAL.tick_interval .. "s")
EZPAL:log("Waiting for players to connect...")

-- Ensure output directory exists
os.execute('mkdir -p "' .. EZPAL.output_dir .. '"')

-- Register the tick callback
RegisterTick(function()
    EZPAL:tick()
end, EZPAL.tick_interval * 1000)
