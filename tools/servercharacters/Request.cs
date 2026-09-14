// Request.proto, hand-rendered.
//
// Upstream generates this file at build time with protobuf-net.MSBuild's protogen,
// which is a .NET tool the offline build here cannot run. Every member below was
// read back out of the SHIPPED Smoothbrain-ServerCharacters 1.4.16 assembly with
// Mono.Cecil - property names, CLR types, ProtoMember tags and Name overrides,
// ProtoMap, and the IExtensible implementation - so the wire format and the C#
// surface are identical to what protogen emits from Request.proto.
#pragma warning disable CS1591, CS0612, CS3021, IDE1006, RCS1036, RCS1057, RCS1085, RCS1192
#nullable disable
namespace ServerCharacters
{
	[global::ProtoBuf.ProtoContract]
	public partial class WebinterfacePlayer : global::ProtoBuf.IExtensible
	{
		private global::ProtoBuf.IExtension __pbn__extensionData;
		global::ProtoBuf.IExtension global::ProtoBuf.IExtensible.GetExtensionObject(bool createIfMissing)
			=> global::ProtoBuf.Extensible.GetExtensionObject(ref __pbn__extensionData, createIfMissing);

		[global::ProtoBuf.ProtoMember(1, Name = @"id")]
		[global::System.ComponentModel.DefaultValue("")]
		public string Id { get; set; } = "";

		[global::ProtoBuf.ProtoMember(2, Name = @"name")]
		[global::System.ComponentModel.DefaultValue("")]
		public string Name { get; set; } = "";

		[global::ProtoBuf.ProtoMember(3)]
		public Statistics statistics { get; set; }

		[global::ProtoBuf.ProtoMember(4)]
		public Position position { get; set; }

		[global::ProtoBuf.ProtoContract]
		public partial class Statistics : global::ProtoBuf.IExtensible
		{
			private global::ProtoBuf.IExtension __pbn__extensionData;
			global::ProtoBuf.IExtension global::ProtoBuf.IExtensible.GetExtensionObject(bool createIfMissing)
				=> global::ProtoBuf.Extensible.GetExtensionObject(ref __pbn__extensionData, createIfMissing);

			[global::ProtoBuf.ProtoMember(1)]
			public long lastTouch { get; set; }

			[global::ProtoBuf.ProtoMember(6, Name = @"stats")]
			[global::ProtoBuf.ProtoMap]
			public global::System.Collections.Generic.Dictionary<string, float> Stats { get; } = new global::System.Collections.Generic.Dictionary<string, float>();
		}

		[global::ProtoBuf.ProtoContract]
		public partial class Position : global::ProtoBuf.IExtensible
		{
			private global::ProtoBuf.IExtension __pbn__extensionData;
			global::ProtoBuf.IExtension global::ProtoBuf.IExtensible.GetExtensionObject(bool createIfMissing)
				=> global::ProtoBuf.Extensible.GetExtensionObject(ref __pbn__extensionData, createIfMissing);

			[global::ProtoBuf.ProtoMember(1, Name = @"x")]
			public float X { get; set; }

			[global::ProtoBuf.ProtoMember(2, Name = @"y")]
			public float Y { get; set; }

			[global::ProtoBuf.ProtoMember(3, Name = @"z")]
			public float Z { get; set; }
		}
	}

	[global::ProtoBuf.ProtoContract]
	public partial class PlayerList : global::ProtoBuf.IExtensible
	{
		private global::ProtoBuf.IExtension __pbn__extensionData;
		global::ProtoBuf.IExtension global::ProtoBuf.IExtensible.GetExtensionObject(bool createIfMissing)
			=> global::ProtoBuf.Extensible.GetExtensionObject(ref __pbn__extensionData, createIfMissing);

		[global::ProtoBuf.ProtoMember(1, Name = @"playerList")]
		public global::System.Collections.Generic.List<WebinterfacePlayer> playerLists { get; } = new global::System.Collections.Generic.List<WebinterfacePlayer>();
	}

	[global::ProtoBuf.ProtoContract]
	public partial class WebinterfaceMod : global::ProtoBuf.IExtensible
	{
		private global::ProtoBuf.IExtension __pbn__extensionData;
		global::ProtoBuf.IExtension global::ProtoBuf.IExtensible.GetExtensionObject(bool createIfMissing)
			=> global::ProtoBuf.Extensible.GetExtensionObject(ref __pbn__extensionData, createIfMissing);

		[global::ProtoBuf.ProtoMember(1, Name = @"guid")]
		[global::System.ComponentModel.DefaultValue("")]
		public string Guid { get; set; } = "";

		[global::ProtoBuf.ProtoMember(2, Name = @"name")]
		[global::System.ComponentModel.DefaultValue("")]
		public string Name { get; set; } = "";

		[global::ProtoBuf.ProtoMember(3, Name = @"version")]
		[global::System.ComponentModel.DefaultValue("")]
		public string Version { get; set; } = "";

		[global::ProtoBuf.ProtoMember(4)]
		public long lastUpdate { get; set; }

		[global::ProtoBuf.ProtoMember(5)]
		[global::System.ComponentModel.DefaultValue("")]
		public string modPath { get; set; } = "";

		[global::ProtoBuf.ProtoMember(6)]
		[global::System.ComponentModel.DefaultValue("")]
		public string configPath { get; set; } = "";
	}

	[global::ProtoBuf.ProtoContract]
	public partial class ModList : global::ProtoBuf.IExtensible
	{
		private global::ProtoBuf.IExtension __pbn__extensionData;
		global::ProtoBuf.IExtension global::ProtoBuf.IExtensible.GetExtensionObject(bool createIfMissing)
			=> global::ProtoBuf.Extensible.GetExtensionObject(ref __pbn__extensionData, createIfMissing);

		[global::ProtoBuf.ProtoMember(1, Name = @"modList")]
		public global::System.Collections.Generic.List<WebinterfaceMod> modLists { get; } = new global::System.Collections.Generic.List<WebinterfaceMod>();
	}

	[global::ProtoBuf.ProtoContract]
	public partial class ServerConfig : global::ProtoBuf.IExtensible
	{
		private global::ProtoBuf.IExtension __pbn__extensionData;
		global::ProtoBuf.IExtension global::ProtoBuf.IExtensible.GetExtensionObject(bool createIfMissing)
			=> global::ProtoBuf.Extensible.GetExtensionObject(ref __pbn__extensionData, createIfMissing);

		[global::ProtoBuf.ProtoMember(1)]
		[global::System.ComponentModel.DefaultValue("")]
		public string serverName { get; set; } = "";

		[global::ProtoBuf.ProtoMember(2)]
		public int processId { get; set; }

		[global::ProtoBuf.ProtoMember(3)]
		[global::System.ComponentModel.DefaultValue("")]
		public string pluginsPath { get; set; } = "";

		[global::ProtoBuf.ProtoMember(4)]
		[global::System.ComponentModel.DefaultValue("")]
		public string patchersPath { get; set; } = "";

		[global::ProtoBuf.ProtoMember(5)]
		[global::System.ComponentModel.DefaultValue("")]
		public string configPath { get; set; } = "";

		[global::ProtoBuf.ProtoMember(6)]
		[global::System.ComponentModel.DefaultValue("")]
		public string savePath { get; set; } = "";
	}

	[global::ProtoBuf.ProtoContract]
	public partial class Maintenance : global::ProtoBuf.IExtensible
	{
		private global::ProtoBuf.IExtension __pbn__extensionData;
		global::ProtoBuf.IExtension global::ProtoBuf.IExtensible.GetExtensionObject(bool createIfMissing)
			=> global::ProtoBuf.Extensible.GetExtensionObject(ref __pbn__extensionData, createIfMissing);

		[global::ProtoBuf.ProtoMember(1)]
		public long startTime { get; set; }

		[global::ProtoBuf.ProtoMember(2)]
		public bool maintenanceActive { get; set; }
	}

	[global::ProtoBuf.ProtoContract]
	public partial class IngameMessage : global::ProtoBuf.IExtensible
	{
		private global::ProtoBuf.IExtension __pbn__extensionData;
		global::ProtoBuf.IExtension global::ProtoBuf.IExtensible.GetExtensionObject(bool createIfMissing)
			=> global::ProtoBuf.Extensible.GetExtensionObject(ref __pbn__extensionData, createIfMissing);

		[global::ProtoBuf.ProtoMember(1, Name = @"steamId")]
		public global::System.Collections.Generic.List<string> steamIds { get; } = new global::System.Collections.Generic.List<string>();

		[global::ProtoBuf.ProtoMember(2, Name = @"message")]
		[global::System.ComponentModel.DefaultValue("")]
		public string Message { get; set; } = "";
	}

	[global::ProtoBuf.ProtoContract]
	public partial class RaiseSkill : global::ProtoBuf.IExtensible
	{
		private global::ProtoBuf.IExtension __pbn__extensionData;
		global::ProtoBuf.IExtension global::ProtoBuf.IExtensible.GetExtensionObject(bool createIfMissing)
			=> global::ProtoBuf.Extensible.GetExtensionObject(ref __pbn__extensionData, createIfMissing);

		[global::ProtoBuf.ProtoMember(1, Name = @"id")]
		[global::System.ComponentModel.DefaultValue("")]
		public string Id { get; set; } = "";

		[global::ProtoBuf.ProtoMember(2, Name = @"name")]
		[global::System.ComponentModel.DefaultValue("")]
		public string Name { get; set; } = "";

		[global::ProtoBuf.ProtoMember(3)]
		[global::System.ComponentModel.DefaultValue("")]
		public string skillName { get; set; } = "";

		[global::ProtoBuf.ProtoMember(4, Name = @"level")]
		public int Level { get; set; }
	}

	[global::ProtoBuf.ProtoContract]
	public partial class ResetSkill : global::ProtoBuf.IExtensible
	{
		private global::ProtoBuf.IExtension __pbn__extensionData;
		global::ProtoBuf.IExtension global::ProtoBuf.IExtensible.GetExtensionObject(bool createIfMissing)
			=> global::ProtoBuf.Extensible.GetExtensionObject(ref __pbn__extensionData, createIfMissing);

		[global::ProtoBuf.ProtoMember(1, Name = @"id")]
		[global::System.ComponentModel.DefaultValue("")]
		public string Id { get; set; } = "";

		[global::ProtoBuf.ProtoMember(2, Name = @"name")]
		[global::System.ComponentModel.DefaultValue("")]
		public string Name { get; set; } = "";

		[global::ProtoBuf.ProtoMember(3)]
		[global::System.ComponentModel.DefaultValue("")]
		public string skillName { get; set; } = "";
	}

	[global::ProtoBuf.ProtoContract]
	public partial class GiveItem : global::ProtoBuf.IExtensible
	{
		private global::ProtoBuf.IExtension __pbn__extensionData;
		global::ProtoBuf.IExtension global::ProtoBuf.IExtensible.GetExtensionObject(bool createIfMissing)
			=> global::ProtoBuf.Extensible.GetExtensionObject(ref __pbn__extensionData, createIfMissing);

		[global::ProtoBuf.ProtoMember(1, Name = @"id")]
		[global::System.ComponentModel.DefaultValue("")]
		public string Id { get; set; } = "";

		[global::ProtoBuf.ProtoMember(2, Name = @"name")]
		[global::System.ComponentModel.DefaultValue("")]
		public string Name { get; set; } = "";

		[global::ProtoBuf.ProtoMember(3)]
		[global::System.ComponentModel.DefaultValue("")]
		public string itemName { get; set; } = "";

		[global::ProtoBuf.ProtoMember(4)]
		public int itemQuantity { get; set; }
	}
}
#pragma warning restore CS1591, CS0612, CS3021, IDE1006, RCS1036, RCS1057, RCS1085, RCS1192
