import type { RadiomicsConfig, RadiomicsFeatureDefinition } from './types'
import { planDependencies, planDraftErrors, planExecutionBlockers, discretisationErrors, executableConfig, type RadiomicsPlanDraft } from './plan'
export function validationErrors(config:RadiomicsConfig,draft:RadiomicsPlanDraft,catalog:RadiomicsFeatureDefinition[],modality='') {
 const errors=[...planDraftErrors(draft,config,catalog),...planExecutionBlockers(config,draft,catalog),...discretisationErrors(config,draft.selectedKeys,catalog)]
 const positive=(v:unknown)=>typeof v==='number'&&Number.isFinite(v)&&v>0
 if(config.resampling!=='none'&&(config.resampling.length!==3||!config.resampling.every(positive)))errors.push('目标体素间距须为三个有限正数')
 if(config.bin_width!==null&&!positive(config.bin_width))errors.push('兼容分箱宽度须为有限正数，或留空使用模态默认值')
 if(!positive(config.normalization_scale??100)||(config.normalization_scale??100)>10000)errors.push('归一化倍率须在 (0, 10000] 内')
 const dependencies=planDependencies(draft.selectedKeys,catalog)
 if(dependencies.custom&&modality&&modality.toUpperCase()!=='CT')errors.push('31 项自定义增量公式适用于肺 CT，当前检查模态不适用')
 return [...new Set(errors)]
}
// This is an inspectable draft contract, never a replacement for a signed server receipt.
export function reviewFingerprint(scope:unknown,config:RadiomicsConfig,draft:RadiomicsPlanDraft){return JSON.stringify([scope,executableConfig(config,draft),draft.selectedKeys])}
