/**
 * 用户生成媒体的 gallery 项(image / video / avatar / voice-clone 共用)。
 *
 * 六十三续:之前 4 处 useState<any[]>(),失去类型检查,gallery item shape
 * 散落在各页面。这是统一定义,场景字段全 optional 兼容现存数据。
 */
export interface GalleryItem {
  url: string;
  prompt?: string;
  label?: string;
  mode?: string;        // voice-clone 用("克隆" / "TTS")
  time: number;
}
