# dopeIPTV Project Documentation                                                                                                                                                              
                                                                                                                                                                                              
                                                                                                                                                                                              
                                                                                                                                                                                              
## Project Purpose                                                                                                                                                                            
                                                                                                                                                                                              
                                                                                                                                                                                              
                                                                                                                                                                                              
dopeIPTV is an elegant IPTV client for Linux and macOS with a macOS-inspired dark interface. It provides a graphical interface to watch IPTV content from Xtream Codes servers, supporting    
live TV, movies, and series with Electronic Program Guide (EPG) information. The application offers embedded playback via mpv or VLC players, along with features such as favorites, history  
tracking, parental controls, and recording capabilities.                                                                                                                                      
                                                                                                                                                                                              
                                                                                                                                                                                              
                                                                                                                                                                                              
## Architecture                                                                                                                                                                               
                                                                                                                                                                                              
                                                                                                                                                                                              
                                                                                                                                                                                              
The application follows a modular architecture using PyQt6 for the GUI:                                                                                                                       
                                                                                                                                                                                              
                                                                                                                                                                                              
                                                                                                                                                                                              
- **Core Components**: Xtream API client, EPG guide parser, playback managers (embedded mpv, external players)                                                                                
                                                                                                                                                                                              
- **Data Management**: Settings persistence via QSettings, favorites/history storage, playlist management                                                                                     
                                                                                                                                                                                              
- **Threading**: Uses QThreadPool for background operations like API calls and file downloads                                                                                                 
                                                                                                                                                                                              
- **UI Components**: Custom widgets with styled rendering using Qt's theming system                                                                                                           
                                                                                                                                                                                              
                                                                                                                                                                                              
                                                                                                                                                                                              
The architecture separates concerns between:                                                                                                                                                  
                                                                                                                                                                                              
- UI layer (MainWindow, widgets)                                                                                                                                                              
                                                                                                                                                                                              
- Data access layer (XtreamClient, XmltvGuide)                                                                                                                                                
                                                                                                                                                                                              
- Business logic layer (RecordingManager, PlaylistStore)                                                                                                                                      
                                                                                                                                                                                              
- Playback layer (EmbeddedPlayer, MpvIpcPlayer)                                                                                                                                               
                                                                                                                                                                                              
                                                                                                                                                                                              
                                                                                                                                                                                              
## Coding Rules for Future Changes                                                                                                                                                            
                                                                                                                                                                                              
                                                                                                                                                                                              
                                                                                                                                                                                              
1. **Code Style**: Follow PEP 8 style guidelines with consistent indentation (4 spaces)                                                                                                       
                                                                                                                                                                                              
2. **Error Handling**: Always handle exceptions gracefully and provide meaningful error messages                                                                                              
                                                                                                                                                                                              
3. **Threading**: Use QThreadPool for background operations; never block the main thread                                                                                                      
                                                                                                                                                                                              
4. **Memory Management**: Be mindful of memory usage, especially with large channel lists                                                                                                     
                                                                                                                                                                                              
5. **UI Responsiveness**: Keep UI updates on the main thread; use signals/slots for cross-thread communication                                                                                
                                                                                                                                                                                              
6. **Resource Cleanup**: Always clean up resources (files, processes, network connections) in shutdown handlers                                                                               
                                                                                                                                                                                              
7. **Documentation**: Add docstrings to all public methods and classes                                                                                                                        
                                                                                                                                                                                              
8. **Dependencies**: Minimize external dependencies; document any new additions clearly                                                                                                       
                                                                                                                                                                                              
9. **Language**: All code, comments, README files, documentation, and commit messages must be written in English                                                                              
                                                                                                                                                                                              
10. **Communication**: Communicate with the user in Swedish                                                                                                                                   
                                                                                                                                                                                              
                                                                                                                                                                                              
                                                                                                                                                                                              
## Areas Requiring Caution                                                                                                                                                                    
                                                                                                                                                                                              
                                                                                                                                                                                              
                                                                                                                                                                                              
1. **Threading**: The application uses multiple threads for network operations and background tasks. Be careful with shared state between threads.                                            
                                                                                                                                                                                              
2. **Resource Management**: Playback managers (mpv, VLC) require proper cleanup to avoid resource leaks.                                                                                      
                                                                                                                                                                                              
3. **Network Operations**: API calls can fail or timeout; implement robust retry logic where appropriate.                                                                                     
                                                                                                                                                                                              
4. **File System Access**: Recording functionality requires proper file system permissions and error handling.                                                                                
                                                                                                                                                                                              
5. **Embedded Player**: The libmpv integration is complex and requires careful handling of OpenGL contexts.                                                                                   
                                                                                                                                                                                              
6. **Settings Persistence**: Changes to settings must be properly saved and loaded across sessions.                                                                                           
                                                                                                                                                                                              
7. **External Dependencies**: The application depends on external tools (ffmpeg, mpv, VLC) that may not be available.                                                                         
                                                                                                                                                                                              
                                                                                                                                                                                              
                                                                                                                                                                                              
## Preferred Development Workflow                                                                                                                                                             
                                                                                                                                                                                              
                                                                                                                                                                                              
                                                                                                                                                                                              
1. **Incremental Changes**: Prefer small incremental changes over large refactorings                                                                                                          
                                                                                                                                                                                              
2. **Planning**: Before making significant code changes, explain the plan in Swedish                                                                                                          
                                                                                                                                                                                              
3. **Functionality Preservation**: Always preserve existing functionality                                                                                                                     
                                                                                                                                                                                              
4. **Destructive Changes**: Ask before making destructive changes                                                                                                                             
                                                                                                                                                                                              
5. **Code Patterns**: Always check existing code patterns before introducing new approaches                                                                                                   
                                                                                                                                                                                              
6. **Refactoring**: Do not refactor large parts of the application unless explicitly requested                                                                                                
                                                                                                                                                                                              
7. **Inspection**: Before modifying code, inspect the relevant existing implementation first                                                                                                  
                                                                                                                                                                                              
8. **Explanation**: Explain the proposed changes and expected impact before making modifications                                                                                              
                                                                                                                                                                                              
9. **Minimal Changes**: Prefer minimal changes that solve the problem                                                                                                                         
                                                                                                                                                                                              
10. **Summary**: After changes, summarize modified files and explain why each change was made                                                                                                 
                                                                                                                                                                                              
                                                                                                                                                                                              
                                                                                                                                                                                              
## Git Workflow                                                                                                                                                                               
                                                                                                                                                                                              
                                                                                                                                                                                              
                                                                                                                                                                                              
- Commit messages must always be written in English.                                                                                                                                          
                                                                                                                                                                                              
- Commit messages should clearly describe the purpose of the change.                                                                                                                          
                                                                                                                                                                                              
- Do not commit unrelated changes.                                                                                                                                                            
                                                                                                                                                                                              
- Never create commits without explaining what was changed.   



## AI Assistant Behavior

- Do not assume missing information. Ask questions when requirements are unclear.
- Do not invent APIs, classes, files, or existing functionality.
- When unsure, inspect the codebase before suggesting solutions.
- Prefer explaining trade-offs instead of choosing complex solutions automatically.
- Never remove existing features unless explicitly requested.
- When creating new features, consider how they integrate with the existing architecture.                                                                                                                                
- Explain technical concepts clearly in Swedish when communicating with the user.
- Remember that the user may not be a professional developer; provide practical guidance instead of only technical answers.                                                                                                                                                                                              
                                                                                                                                                                                              
                                                                                                                                                                                              
The development workflow emphasizes stability, maintainability, and user experience while keeping the codebase clean and well-documented. As a solo developer using AI assistance, focus on   
making thoughtful, incremental improvements that enhance the application without breaking existing functionality.     
