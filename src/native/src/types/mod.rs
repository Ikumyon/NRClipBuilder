//! Nimby Rails .nrclip data types with version-aware serialization.

use crate::wire::{PayloadReader, PayloadWriter};
use anyhow::Result;

// ══════════════════════════════════════════════════════════════════════
// Serialization traits
// ══════════════════════════════════════════════════════════════════════

/// Deserialize from the game's binary wire format.
pub trait NrclipRead: Sized {
    fn nrclip_read(r: &mut PayloadReader, ver: u32) -> Result<Self>;
}

/// Serialize to the game's binary wire format.
pub trait NrclipWrite {
    fn nrclip_write(&self, w: &mut PayloadWriter, ver: u32);
}

// ══════════════════════════════════════════════════════════════════════
// Type modules
// ══════════════════════════════════════════════════════════════════════

mod building;
mod collection;
mod demand;
mod kinds;
mod mod_meta;
mod signal;
mod station;
mod track;

// ══════════════════════════════════════════════════════════════════════
// Re-exports
// ══════════════════════════════════════════════════════════════════════

pub use building::Building;
pub use collection::{Clip, Collection};
pub use demand::Demand;
pub use kinds::{BuildingKind, TrackKind, TrackKindHorizon, TrackTexture};
pub use mod_meta::{ModMeta, ModRelFile};
pub use signal::Signal;
pub use station::StationGroup;
pub use track::Track;
